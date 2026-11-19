# Models

The five ONNX models the Studio uses are **not in git** (they're third-party binaries). They ship inside the
install bundle on [Hugging Face](https://huggingface.co/Walkerb09/Director-demo), which the Vast.ai template
installs for you. To run from this repository instead, copy them into this folder from the bundle
(`payload/ComfyUI-DirectorStudio_v0970_demo.zip`, folder `ComfyUI-DirectorStudio/models/`); `verify_package.py` then
checks every file, these included, against the SHA-256 values below.

| file | what it is | SHA-256 |
|---|---|---|
| `face_detection_yunet_2023mar.onnx` | YuNet face detector (OpenCV Zoo, MIT) | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| `face_recognition_sface_2021dec.onnx` | SFace face recognition (OpenCV Zoo, Apache-2.0) | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` |
| `text_detection_en_ppocrv3_2023may.onnx` | PP-OCRv3 text detector (OpenCV Zoo, Apache-2.0) | `03f550c6b406fda8bf54bd8327815f6c7e2edd98cea02348c93d879254366587` |
| `u2netp.onnx` | U²-Net-p salient-object cut-out (U-2-Net, Apache-2.0) | `309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8` |
| `version-RFB-640.onnx` | Ultra-Light-Fast-Generic-Face-Detector (MIT) | `8f4c659275977e7a3bfbfa339a9c769ad793df50f9c0baa8c14b11baa1646430` |

Each model keeps its own license (the `LICENSE_*` files here).
