# Finish transient worker-release waits before the next rollout

## Observed gap

During the initial activation of revision `18b8c27eb18b65fcf663b0e713df6142ebd39321`,
canonical Deploy required same-candidate continuations while retaining its original groups,
recovery evidence and capacity ceiling. Two failure boundaries were reproduced locally through
the real release functions:

- `Host.observe()` aborts when a changing complete cloud snapshot is rejected, or when the
  concurrent canonical collector commits the same observation sequence first. Fresh observations
  are required, but this transient conflict currently prevents `next_step()` from returning wait.
- Retirement reconciliation may accept `STOPPED` before the obsolete VM and its boot disk have
  disappeared. `transition()` can then return verified, while the outer floor-one template call
  correctly rejects the still-transitional allocation through `disk_fence()`.

Live runs `36948175598`, `36950104628`, `36950750635` and `36952380622` stopped during worker health.
Their generic remote error does not prove which exception occurred in each run. The second
reproduction matches the retained retirement receipt; do not substitute that match for a captured
live traceback. Complete provider read-back subsequently proved the old VMs and disks absent.

## Why this is non-blocking for the settled fleet

Same-candidate continuation through canonical Deploy `36953075943` completed successfully. Both
pools reached staged revision `18b8c27`, with no pending operation or expanded pool; obsolete
allocations were absent. No rollback, journal rewrite, manual processing-row reset or relaxed
fence was used. These release-control gaps do not prevent acceptance of that already-settled
fleet. This is not proof of customer cutover, notification delivery or scale-to-zero/wakeup.

## Revisit trigger and required fix

Resolve this **before the next immutable worker rollout or relying on unattended fleet releases**.
Use the existing release path in `deploy/worker-pools/release.py` and the cloud observation service:

- Represent only the demonstrated transient observation conflicts explicitly; reacquire a full
  fresh snapshot within the existing bounded transition deadline. Keep wrong scope, image,
  ownership and capacity evidence fatal. Never continue with a stale cached snapshot.
- Wait for terminal retirement settlement and complete VM/disk absence before the outer final
  template/fence step. Preserve every strict ownership, inventory and serial-expansion check.
- Report a bounded, nonsecret failure classification through the deployment wrapper so operators
  can distinguish transient observation, retirement settlement and fatal validation failures.
- Test the actual caller chain with a competing observation, changing startup counters, reconciled
  STOPPED allocation, delayed disk deletion, deadline expiry and fatal ownership/capacity errors.

Do not add a blanket retry, increase VM/disk limits, suppress health checks, or introduce another
deployment entrypoint. Deliver and review the fix through the normal Git/CI path.
