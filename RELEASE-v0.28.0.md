# RDR1 Animation Studio v0.28.0

## Animation preview reliability

- The Game folder picker now starts at the standard Steam installation path when present:
  `D:\Program Files (x86)\Steam\steamapps\common\Red Dead Redemption\game`.
- The embedded WebView2 viewer retries the model request if the first navigation
  message is lost during initialization.
- Model-transfer, navigation, and viewer JSON errors are surfaced instead of
  leaving the panel stuck on “Preparing hardware-accelerated 3D preview”.
- Preview cache keys were bumped so stale failed GLBs are regenerated.
- Existing exact and partial skeleton binding behavior is preserved; unsupported
  WAS layouts still report their decoder limitation honestly.
