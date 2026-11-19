# ComfyUI integration

AutoContent talks to ComfyUI over its HTTP and WebSocket API (default `http://127.0.0.1:8188`). Keep
ComfyUI bound to localhost. AutoContent never exposes it.

## Bundled workflows (imported on first start)

| File | Kind | Models it expects (all present on the dev machine) |
|---|---|---|
| `qwen_image_txt2img.json` | txt2img (default) | `qwen_image_2512_fp8_e4m3fn` (UNet), `qwen_2.5_vl_7b_fp8_scaled` (CLIP, type `qwen_image`), `qwen_image_vae` |
| `wan21_i2v_480p.json` | img2vid (opt-in) | `wan2.1_i2v_480p_14B_fp8_scaled`, `umt5_xxl_fp8_e4m3fn_scaled`, `wan_2.1_vae`, VHS_VideoCombine |

Both validated against the live server (ComfyUI 0.41.0). The Qwen workflow produced a 928×1664 image
in **~470 s at 20 steps** on the RTX 4090 Laptop GPU while another process held about 10 GB of VRAM. So the
default is at most **2 ComfyUI scenes per video** (Settings → ComfyUI); the other scenes use product
screenshots or branded title cards. Lower `steps` (Workflow → Edit mappings → Defaults) for speed.
Image-to-video (Wan 2.1 14B) is off by default because it takes several minutes per clip; it also
runs without CLIP-vision conditioning, since no CLIP-vision model is installed.

## Importing your own workflow

1. In ComfyUI choose **Workflow → Export (API)**. The UI-format save will be rejected.
2. ComfyUI Workflows → **Import API-format JSON**. The importer suggests mappings (prompt nodes
   wired into the KSampler, latent width/height, LoadImage, SaveImage prefix).
3. **Edit mappings** to confirm which node input receives each value:
   `positive_prompt, negative_prompt, seed, width, height, length, fps, steps, input_image, filename_prefix, batch_size`.
4. **Validate**. The workflow is checked against `/object_info`: missing custom nodes, model files
   that aren't installed, and mappings that point at missing nodes or inputs.
5. Mark it **Default** for its kind, or scope it to one application or content type.

Outputs are collected from `SaveImage`, `SaveVideo`, `VHS_VideoCombine` and similar nodes through `/view`.
Image outputs get Ken Burns motion in FFmpeg. Video outputs are scaled or cropped to 9:16 and looped or trimmed.

## Failure behaviour
- **Server unreachable:** logged once per render with the fix ("Start ComfyUI… or fix the URL"). All
  scenes fall back to screenshots or title cards. The video still renders.
- **Execution error or timeout:** the prompt is cancelled (`/queue` delete or `/interrupt`), the asset
  is recorded as failed with the error, and that scene falls back.
- The **generation log** tab of each video shows per-scene source, prompt, GPU seconds and errors.
