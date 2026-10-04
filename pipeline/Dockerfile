# The official COLMAP image is built with CUDA. Ubuntu's apt `colmap` package is not,
# and without CUDA the dense step silently produces no point cloud.
FROM colmap/colmap:latest
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip libgl1 libglib2.0-0 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir runpod requests open3d numpy trimesh \
 || python3 -m pip install --no-cache-dir --break-system-packages runpod requests open3d numpy trimesh

COPY handler.py /app/handler.py
WORKDIR /app
CMD ["python3", "-u", "handler.py"]
