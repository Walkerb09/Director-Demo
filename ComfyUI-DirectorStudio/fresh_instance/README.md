# Fresh Vast instance

Use **Director_FreshVast_FullInstall_V090.zip** (delivered next to this package). It installs everything on a
fresh instance: the full BF16 H3 models (downloaded from Hugging Face on the instance, hash-checked), VideoHelperSuite,
this Director Studio, and the Qwen observer chain with its own Python env and Qwen3-VL-8B.

    cd /workspace && unzip Director_FreshVast_FullInstall_V090.zip -d DirectorFullInstall && bash DirectorFullInstall/install.sh
