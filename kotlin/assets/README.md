# Assets

Patch manifests, payloads and bootstrap scripts will be placed here during
Stage 2/4. The Python build tools in the repository root remain the source of
truth for generated payloads:

- `../../patches/` — manifests and generated `.orig`/`.patched` files;
- `../../bootstrap/v2/` — bootstrap payload.

Do not commit `firmware/update.bin`, generated `.orig`/`.patched` files or
release archives.
