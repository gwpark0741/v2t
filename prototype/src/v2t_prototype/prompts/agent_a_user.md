Analyze the attached full video and return the Stage 03 EntityRegistry.

The attached video is the primary visual source.
Use the metadata and authoritative cuts below as structural context only —
do not create per-cut registries. Return one unified global registry for the full video.

Authoritative video metadata:
- video_path: {video_path}
- fps: {fps}
- duration_seconds: {duration_seconds}
- resolution: {width}x{height}

Authoritative cuts (produce one cut_mapping entry per cut, using the cut id
field as cut_id in your output):
{cuts_json}
