"""ScanForge RunPod serverless worker.

Fetches photos over HTTPS, runs COLMAP (sparse+dense), builds a mesh with
Open3D (Poisson), writes GLB (Blender) + STL (3D printer) + low-poly preview,
then POSTs results back to the backend.
"""
import os
import subprocess
import traceback

import requests

JOB_DIR = "/workspace"


def _download(urls, dest):
    os.makedirs(dest, exist_ok=True)
    for i, u in enumerate(urls):
        ext = os.path.splitext(u.split("?")[0])[1] or ".jpg"
        p = os.path.join(dest, f"{i:04d}{ext}")
        r = requests.get(u, timeout=120)
        r.raise_for_status()
        with open(p, "wb") as f:
            f.write(r.content)


def _run(cmd):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def _reconstruct(img_dir, ws):
    """COLMAP automatic reconstruction (sparse + dense)."""
    _run([
        "colmap", "automatic_reconstructor",
        "--workspace_path", ws,
        "--image_path", img_dir,
        "--quality", "medium",
        "--single_camera", "1",
        "--camera_model", "OPENCV",
        "--use_gpu", "1",
    ])
    dense_root = os.path.join(ws, "dense")
    for root, _dirs, files in os.walk(dense_root):
        if "fused.ply" in files:
            return os.path.join(root, "fused.ply")
    raise RuntimeError("dense reconstruction produced no fused.ply")


def _mesh(fused_ply, out_stl, out_glb, out_preview, log):
    import open3d as o3d

    pcd = o3d.io.read_point_cloud(fused_ply)
    log.append(f"points: {len(pcd.points)}")
    pcd = pcd.voxel_down_sample(0.002)
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.02, max_nn=30))
    pcd.orient_normals_consistent_tangent_plane(15)
    mesh, _ = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=9, linear_fit=True
    )
    bbox = pcd.get_axis_aligned_bounding_box()
    mesh = mesh.crop(bbox)
    mesh = mesh.filter_smooth_simple(number_of_iterations=2)

    o3d.io.write_triangle_mesh(out_stl, mesh)   # 3D printer
    o3d.io.write_triangle_mesh(out_glb, mesh)   # Blender (glTF binary)

    target = max(5000, len(mesh.vertices) // 10)
    simple = mesh.simplify_quadric_decimation(target)
    o3d.io.write_triangle_mesh(out_preview, simple)
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

        files = {}
        for name in ("model.stl", "model.glb", "preview.glb"):
            with open(os.path.join(out, name), "rb") as f:
                files[name.split(".")[1]] = (name, f.read())
        r = requests.post(result_url, files=files,
                          data={"log": "\n".join(log_lines)}, timeout=300)
        r.raise_for_status()
        return {"ok": True, "log": log_lines}
    except Exception as e:  # noqa: BLE001
        log_lines.append(traceback.format_exc())
        try:
            requests.post(result_url, data={"log": "\n".join(log_lines)}, timeout=60)
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    import runpod
    runpod.serverless.start({"handler": handler})
