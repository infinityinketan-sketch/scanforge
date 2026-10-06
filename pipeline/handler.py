"""ScanForge RunPod serverless worker.

Fetches photos over HTTPS, runs COLMAP (sparse+dense), builds a mesh with
Open3D (Poisson), writes GLB (Blender) + STL (3D printer) + low-poly preview,
then POSTs results back to the backend.
"""
import os
import shutil
import subprocess
import traceback

import requests

JOB_DIR = os.getenv("JOB_DIR", "/workspace")
QUALITY = os.getenv("COLMAP_QUALITY", "medium")  # low | medium | high | extreme


def _download(urls, dest):
    os.makedirs(dest, exist_ok=True)
    for i, u in enumerate(urls):
        ext = os.path.splitext(u.split("?")[0])[1] or ".jpg"
        p = os.path.join(dest, f"{i:04d}{ext}")
        with requests.get(u, timeout=120, stream=True) as r:
            r.raise_for_status()
            with open(p, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)


def _run(cmd):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def _reconstruct_pycolmap(img_dir, ws):
    """Same steps as `colmap automatic_reconstructor`, via the pycolmap-cuda12 wheel (used on Colab)."""
    import pycolmap

    if not pycolmap.has_cuda:
        raise RuntimeError("pycolmap has no CUDA: install pycolmap-cuda12 on a GPU runtime")
    max_size = {"low": 1000, "medium": 1600, "high": 2400, "extreme": 3200}.get(QUALITY, 1600)
    db = os.path.join(ws, "database.db")
    sparse, dense = os.path.join(ws, "sparse"), os.path.join(ws, "dense")
    os.makedirs(sparse, exist_ok=True)

    print("+ features", flush=True)
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "OPENCV"
    extraction = pycolmap.FeatureExtractionOptions()
    extraction.max_image_size = max_size * 2   # feature detection likes more pixels than stereo
    pycolmap.extract_features(db, img_dir, camera_mode=pycolmap.CameraMode.SINGLE,
                              reader_options=reader, extraction_options=extraction)
    print("+ matching", flush=True)
    pycolmap.match_exhaustive(db)
    print("+ sparse mapping", flush=True)
    maps = pycolmap.incremental_mapping(db, img_dir, sparse)
    if not maps:
        raise RuntimeError("could not match photos: retake with more overlap and texture")
    best = max(maps, key=lambda k: maps[k].num_reg_images())
    print(f"+ registered {maps[best].num_reg_images()} photos", flush=True)

    print("+ undistort", flush=True)
    undistort = pycolmap.UndistortCameraOptions()
    undistort.max_image_size = max_size
    pycolmap.undistort_images(dense, os.path.join(sparse, str(best)), img_dir,
                              undistort_options=undistort)
    print("+ dense stereo", flush=True)
    pm = pycolmap.PatchMatchOptions()
    pm.max_image_size = max_size
    pm.geom_consistency = True
    pycolmap.patch_match_stereo(dense, options=pm)
    print("+ fusion", flush=True)
    fused = os.path.join(dense, "fused.ply")
    rec = pycolmap.stereo_fusion(fused, dense, output_type="PLY")
    if not os.path.exists(fused) and rec is not None and rec.num_points3D():
        rec.export_PLY(fused)
    if not os.path.exists(fused):
        raise RuntimeError("camera poses found but dense step produced no points")
    return fused


def _reconstruct(img_dir, ws):
    """COLMAP automatic reconstruction (sparse + dense). Dense stereo needs a CUDA build of COLMAP."""
    if os.getenv("COLMAP_BACKEND") == "pycolmap":
        return _reconstruct_pycolmap(img_dir, ws)
    _run([
        "colmap", "automatic_reconstructor",
        "--workspace_path", ws,
        "--image_path", img_dir,
        "--quality", QUALITY,
        "--single_camera", "1",
        "--camera_model", "OPENCV",
        "--use_gpu", "1",
    ])
    dense_root = os.path.join(ws, "dense")
    for root, _dirs, files in os.walk(dense_root):
        if "fused.ply" in files:
            return os.path.join(root, "fused.ply")
    if os.path.isdir(os.path.join(ws, "sparse")):
        raise RuntimeError("camera poses found but dense step produced no points "
                           "(is COLMAP built with CUDA?)")
    raise RuntimeError("could not match photos: retake with more overlap and texture")


def _write_glb(mesh, path):
    """Write a standard binary glTF with vertex colours. Open3D's own .glb writer embeds the
    geometry as base64 in the JSON with no binary chunk, which many viewers (incl. Assimp) reject."""
    import numpy as np
    import trimesh

    colors = None
    if mesh.has_vertex_colors():
        colors = (np.clip(np.asarray(mesh.vertex_colors), 0, 1) * 255).astype(np.uint8)
    tm = trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices),
        faces=np.asarray(mesh.triangles),
        vertex_normals=np.asarray(mesh.vertex_normals),
        vertex_colors=colors,
        process=False,
    )
    tm.export(path, file_type="glb")


def _read_poses(images_bin):
    """Camera centres and viewing directions from a COLMAP images.bin (world coordinates)."""
    import struct

    import numpy as np

    poses = []
    if not os.path.exists(images_bin):
        return poses
    with open(images_bin, "rb") as f:
        (n,) = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            _id, qw, qx, qy, qz, tx, ty, tz, _cam = struct.unpack("<I7dI", f.read(64))
            while f.read(1) != b"\0":   # image name
                pass
            (npts,) = struct.unpack("<Q", f.read(8))
            f.seek(24 * npts, 1)
            R = np.array([
                [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy)],
                [2 * (qx * qy + qw * qz), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qw * qx)],
                [2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx * qx + qy * qy)],
            ])
            poses.append((-R.T @ np.array([tx, ty, tz]), R[2]))
    return poses


def _isolate_object(pcd, poses, log):
    """Keep the object the photos were aimed at; drop the table, floor and background.

    Returns the cropped cloud and an 'up' vector (or None when the camera path can't tell)."""
    import numpy as np

    C = np.array([c for c, _ in poses])
    D = np.array([d / np.linalg.norm(d) for _, d in poses])
    # The object sits where the cameras' viewing rays (nearly) meet.
    A = np.zeros((3, 3))
    b = np.zeros(3)
    for c, d in zip(C, D):
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ c
    target = np.linalg.lstsq(A, b, rcond=None)[0]
    cam_dist = float(np.median(np.linalg.norm(C - target, axis=1)))

    pts = np.asarray(pcd.points)
    keep = np.linalg.norm(pts - target, axis=1) < 0.5 * cam_dist
    if keep.sum() < 3000:
        log.append("object crop skipped (too few points near the aim point)")
        return pcd, None
    obj = pcd.select_by_index(np.flatnonzero(keep))

    # Cameras walked around the object lie roughly on a plane parallel to the table.
    up = None
    centred = C - C.mean(axis=0)
    if len(C) >= 6:
        _u, s, vt = np.linalg.svd(centred, full_matrices=False)
        if s[2] < 0.5 * s[1]:            # a real loop around the object, not a straight line
            up = vt[2] if vt[2] @ (C.mean(axis=0) - target) > 0 else -vt[2]

    if up is not None:
        # Remove the supporting surface: the largest plane facing 'up' below the object's centre.
        diag = float(np.linalg.norm(obj.get_max_bound() - obj.get_min_bound()))
        plane, inliers = obj.segment_plane(distance_threshold=diag / 150, ransac_n=3, num_iterations=1000)
        n = np.asarray(plane[:3])
        n_len = np.linalg.norm(n)
        if abs(n @ up) / n_len > 0.9 and len(inliers) > 0.1 * len(obj.points):
            sign = 1.0 if n @ up > 0 else -1.0
            height = (np.asarray(obj.points) @ (n * sign) + plane[3] * sign) / n_len
            if (target @ (n * sign) + plane[3] * sign) / n_len > 0:     # object sits above it
                obj = obj.select_by_index(np.flatnonzero(height > diag / 100))
                log.append("removed supporting surface")
    log.append(f"object points: {len(obj.points)}")
    return obj, up


def _mesh(fused_ply, out_stl, out_glb, out_preview, log, poses=None):
    import numpy as np
    import open3d as o3d

    pcd = o3d.io.read_point_cloud(fused_ply)
    log.append(f"points: {len(pcd.points)}")
    if len(pcd.points) < 1000:
        raise RuntimeError("too few 3D points to build a mesh")

    if poses is None:
        poses = _read_poses(os.path.join(os.path.dirname(fused_ply), "sparse", "images.bin"))
    up = None
    if len(poses) >= 3:
        pcd, up = _isolate_object(pcd, poses, log)

    # COLMAP output has arbitrary scale, so size every parameter relative to the object.
    diag = float(np.linalg.norm(pcd.get_max_bound() - pcd.get_min_bound()))
    voxel = diag / 500
    pcd = pcd.voxel_down_sample(voxel)
    pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)

    # Drop floating specks: keep only clusters comparable in size to the main one.
    labels = np.asarray(pcd.cluster_dbscan(eps=voxel * 4, min_points=10))
    if labels.max() >= 0:
        sizes = np.bincount(labels[labels >= 0])
        good = np.flatnonzero(sizes >= 0.2 * sizes.max())
        pcd = pcd.select_by_index(np.flatnonzero(np.isin(labels, good)))
    if len(pcd.points) < 1000:
        raise RuntimeError("too few 3D points on the object: retake with more overlap and texture")

    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 4, max_nn=30))
    if len(poses) >= 3:
        # Every surface point was seen by a camera, so its normal should face the nearest one.
        C = np.array([c for c, _ in poses])
        pts, nrm = np.asarray(pcd.points), np.asarray(pcd.normals)
        nearest = np.concatenate([
            C[np.argmin(((chunk[:, None, :] - C[None]) ** 2).sum(-1), axis=1)]
            for chunk in np.array_split(pts, max(1, len(pts) // 50000))
        ])
        flip = ((nearest - pts) * nrm).sum(1) < 0
        nrm[flip] *= -1
        pcd.normals = o3d.utility.Vector3dVector(nrm)
    else:
        pcd.orient_normals_consistent_tangent_plane(15)

    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=10, linear_fit=True
    )
    # Poisson invents surface where there were no points; trim the least-supported 5%.
    densities = np.asarray(densities)
    mesh.remove_vertices_by_mask(densities < np.quantile(densities, 0.05))
    bbox = pcd.get_axis_aligned_bounding_box()
    mesh = mesh.crop(bbox.scale(1.02, bbox.get_center()))
    # Keep the main surface, drop small disconnected scraps.
    tri_labels, tri_counts, _ = mesh.cluster_connected_triangles()
    tri_labels, tri_counts = np.asarray(tri_labels), np.asarray(tri_counts)
    if len(tri_counts):
        mesh.remove_triangles_by_mask(tri_counts[tri_labels] < 0.1 * tri_counts.max())
    mesh = mesh.filter_smooth_taubin(number_of_iterations=5)   # denoise without shrinking
    mesh.remove_degenerate_triangles()
    mesh.remove_unreferenced_vertices()

    # Stand the model upright (+Y up, as glTF viewers and slicers expect), base on the ground,
    # and size it to 100 units (mm in a slicer) since photos carry no real-world scale.
    if up is not None:
        y = up / np.linalg.norm(up)
        x = np.cross([0.0, 0.0, 1.0] if abs(y[2]) < 0.9 else [1.0, 0.0, 0.0], y)
        x /= np.linalg.norm(x)
        mesh.rotate(np.stack([x, y, np.cross(x, y)]), center=(0, 0, 0))
    v = np.asarray(mesh.vertices)
    lo, hi = v.min(axis=0), v.max(axis=0)
    mesh.translate(-np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2]))
    mesh.scale(100.0 / float((hi - lo).max()), center=(0, 0, 0))

    mesh.compute_vertex_normals()
    mesh.compute_triangle_normals()  # STL writer requires triangle normals

    if not o3d.io.write_triangle_mesh(out_stl, mesh):   # 3D printer
        raise RuntimeError("failed to write STL")
    _write_glb(mesh, out_glb)                            # Blender (glTF binary)

    target = min(len(mesh.triangles), max(20000, len(mesh.triangles) // 10))
    simple = mesh.simplify_quadric_decimation(target)
    simple.compute_vertex_normals()
    _write_glb(simple, out_preview)
    return len(mesh.vertices), len(mesh.triangles)


def handler(event):
    inp = event.get("input", {})
    job_id = inp["job_id"]
    photo_urls = inp["photo_urls"]
    result_url = inp["result_url"]
    ws = os.path.join(JOB_DIR, job_id)
    img_dir = os.path.join(ws, "images")
    log_lines = []
    try:
        _download(photo_urls, img_dir)
        log_lines.append(f"photos: {len(photo_urls)}")
        fused = _reconstruct(img_dir, ws)
        out = os.path.join(ws, "out")
        os.makedirs(out, exist_ok=True)
        nv, nt = _mesh(fused, f"{out}/model.stl", f"{out}/model.glb",
                       f"{out}/preview.glb", log_lines)
        log_lines.append(f"mesh: {nv} verts / {nt} tris")

        # Field names must match the backend's /result parameters exactly.
        names = {"stl": "model.stl", "glb": "model.glb", "preview": "preview.glb"}
        handles = {field: open(os.path.join(out, fn), "rb") for field, fn in names.items()}
        try:
            files = {field: (names[field], fh) for field, fh in handles.items()}
            r = requests.post(result_url, files=files,
                              data={"log": "\n".join(log_lines)}, timeout=600)
        finally:
            for fh in handles.values():
                fh.close()
        r.raise_for_status()
        return {"ok": True, "log": log_lines}
    except Exception as e:  # noqa: BLE001
        log_lines.append(traceback.format_exc())
        # Last line is shown to the user as the failure reason.
        log_lines.append(f"error: {e}")
        try:
            requests.post(result_url, data={"log": "\n".join(log_lines)}, timeout=60)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": str(e)}
    finally:
        # Warm workers reuse the disk; don't let old jobs fill it.
        shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    import runpod
    runpod.serverless.start({"handler": handler})
