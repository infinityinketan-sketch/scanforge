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


def _reconstruct(img_dir, ws):
    """COLMAP automatic reconstruction (sparse + dense). Dense stereo needs a CUDA build of COLMAP."""
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
    """Write a standard binary glTF. Open3D's own .glb writer embeds the geometry as base64
    in the JSON with no binary chunk, which many viewers (incl. Assimp) reject."""
    import numpy as np
    import trimesh

    tm = trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices),
        faces=np.asarray(mesh.triangles),
        vertex_normals=np.asarray(mesh.vertex_normals),
        process=False,
    )
    tm.export(path, file_type="glb")


def _mesh(fused_ply, out_stl, out_glb, out_preview, log):
    import numpy as np
    import open3d as o3d

    pcd = o3d.io.read_point_cloud(fused_ply)
    log.append(f"points: {len(pcd.points)}")
    if len(pcd.points) < 1000:
        raise RuntimeError("too few 3D points to build a mesh")

    # COLMAP output has arbitrary scale, so size every parameter relative to the object.
    diag = float(np.linalg.norm(pcd.get_max_bound() - pcd.get_min_bound()))
    voxel = diag / 600
    pcd = pcd.voxel_down_sample(voxel)
    pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 5, max_nn=30))
    pcd.orient_normals_consistent_tangent_plane(15)

    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=9, linear_fit=True
    )
    # Poisson invents surface where there were no points; trim the least-supported 3%.
    densities = np.asarray(densities)
    mesh.remove_vertices_by_mask(densities < np.quantile(densities, 0.03))
    mesh = mesh.crop(pcd.get_axis_aligned_bounding_box())
    mesh = mesh.filter_smooth_simple(number_of_iterations=2)
    mesh.remove_degenerate_triangles()
    mesh.remove_unreferenced_vertices()
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
