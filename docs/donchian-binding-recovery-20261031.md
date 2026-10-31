# Donchian binding recovery — October 31

GitOps PR #512 changed the effective continuation overlays from runtime digest
6feabc4e2afb42944850a16eb19c616a13ed1c07421bee4305a815f696f0a735 to
0318108dd91b4fb6ef3e2229b129a46d71f1dca7f67b021998d537b9dbfbcfbf without
changing continuation run IDs. The frozen lock covered only base manifests;
the unmarked overlays had no research command for the publisher to recognise.

## Recovery

Only Donchian's observer and init images revert to the immediately preceding
desired digest, 6feabc4e. That is a recovery candidate, not proof of saved-state
compatibility. A read-only init preflight compares stored and computed binding,
artifact and manifest configuration and then invokes the original Donchian
activation verifier. It resumes only if these checks pass.

The init log reports BINDING_MATCH, BINDING_MISMATCH or RUN_MISSING with hashes,
config/artifact match flags, stream count and attestation image. It never prints
DSNs, credentials, full state or strategy history. It writes no database rows.
The ConfigMap contains only this read-only guard, not replacement strategy code.

If the candidate does not match, preserve the state and use the reported hashes
to identify its original artifact/image. Do not keep trying arbitrary images,
rewrite the binding, transplant an attestation or clear streams. A different
artifact needs a separately reviewed new run or explicit compatible migration.

## Prevention and scope

All three continuation patches are now marked frozen, quoted and enrolled in
frozen-research-images.json. Canary and ICT keep their current desired images;
their health/bindings have not been verified here and are not silently rolled
back. The platform companion fix makes release repinning recognise overlays by
the protected workload's identity, including JSON bases and explicit lock paths.

No immutable run ConfigMap, activation boundary, strategy definition, checkpoint,
broker setting or live risk policy is changed. No history is deleted. The
original Donchian run remains selected; there is no new T0 to race against.

Live acceptance requires Flux reconciliation, BINDING_MATCH plus VERIFIED in the
Donchian init log, a Ready observer, and an advancing last-close watermark. The
workspace has no cluster access, so these live facts are not yet established.
