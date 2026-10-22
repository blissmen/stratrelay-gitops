# Frozen observer binding recovery

The automated release updater changed the ICT/SMC runtime image without changing
the immutable run manifest. PR #372 introduced runtime 35fc80be and a later bulk
release (#375) queued another repin. The current 35fc80be image computes binding
1de7563d6c5c6db39964064c56b53963f140ee045216288f2373a69b719dc1a1,
which differs from the saved d7b4aaca5a90b8c36af548746b7161f3f64fb2a4ab4d34c0642418254b6fb873.
It changes the normalized RunConfig shape (transport and entry_quote_max_age_seconds)
and hashes different observer source. The restore guard correctly rejects it.

A credential-free computation-only Pod running the released immutable runtime
e94661478c91cb03766b131d8938100f8dd7fa872232983768c72a07031462ac
reproduces the saved ICT binding and artifact
9b5c739b88a6cbdaafb80ea37c4cfecc9554d67e272d1190b5c04984900cbaaf
using the existing manifest. It was released from platform-tools a4b0715de4df2b27bfe41b70de805219a2714f04.
No database credential, Redis credential or service-account token is mounted in
the proof Pods. They only compute identity; they never run an observer cycle.

Restore only ICT/SMC to that matching image. Retain the canary beaf80a9 and
Donchian V2 19b2f766 artifacts and all existing manifests/checkpoints/bindings.
The candidate JetStream bundle remains suspended/scaled to zero.

Hold the unapplied #375 bulk release at the last applied #374 revision to avoid
rolling unrelated runtimes/collector during this repair. In particular, restore
the paused Job's original image: its immutable template otherwise prevents Flux
from applying any repair. No Job is deleted or force-recreated. The release.json
also returns to that actually deployed release's provenance.

The explicit frozen-research-images.json lock and CI prevent accidental future
repins, including quoted and JSON workloads. YAML bundles also carry a
stratrelay-release: frozen marker and quoted pins for compatibility with the old
updater. The platform-tools updater receives a separate fix to honor the marker
and automatically exclude library observer/event bundles regardless of quoting.
To intentionally change a frozen image, first prove its stored binding matches
or establish a separate fresh run, then review its lock update.

Verification must include repeated successful cycles, advancing persisted health
and evaluation evidence with stable restart counts. Preserve the pre-existing
research execution settings and managed catalog bindings. The final redacted
operator report records actual post-reconciliation evidence separately.
