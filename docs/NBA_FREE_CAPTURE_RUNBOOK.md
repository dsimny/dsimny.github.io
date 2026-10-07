# NBA free evidence capture

Purpose: retain timestamped public-source evidence before it disappears. This lane
does not produce predictions, a database ledger, calibrated injuries or official plays.

The runner is scripts/nba/free_capture.py. Supply absolute --root and --key-file
paths outside every Git worktree. A first run creates a Fernet key; subsequent runs
must retain that key. A missing key with existing ciphertext is a hard refusal.
Copy the key securely into an independent backup before relying on retained evidence.
No independent off-host backup has been configured in Package 1.

Run with --verify after each capture to validate run/observation binding, hashes,
decryptability, raw byte counts and unfinished orphan records. Orphans indicate an
interrupted run: retain them for a recovery importer, never delete to make the check
green. The verifier does not prove an external timestamp or detect deletion of an
entire run and all its files without an independent retained inventory.

Default source set: NBA schedule, NBA Official index, prospective 2026–27 official
injury-report URL, NBA news index. Up to 12 linked NBA-domain injury/availability
pages are captured per run. Team announcements outside the index are not covered
automatically. A --source-config JSON file can add verified official team pages;
sources carry id, kind and url, all HTTPS NBA URLs without credentials/query data.
Do not enable a paid feed or broaden domains in this free lane.

HTTP 200 is fetch evidence only; a shell/challenge page is still semantically
UNVERIFIED. The 2026–27 report URL returned 404 on the initial run; this failure was
preserved. Older report pages are archived raw context, never current-season player
availability. Discovered news includes unparsed pages and is not a complete injury
feed. Until Package 2/source parsers reconcile schedule contents and player/game/date
identities, source coverage remains incomplete.

The chat's hourly “NBA free evidence capture” automation runs the narrow collector
and verifier. It stays quiet for unchanged results and the known unchanged 404,
and reports new actionable source or integrity failures. It does not modify code,
call odds APIs or publish externally. Local scheduled runs require the computer on
and the desktop app running; see https://learn.chatgpt.com/docs/automations?surface=app.
This schedule is not an always-on production host or a near-tipoff injury guarantee.

Rollback: pause the capture automation; retain existing objects, observations, run
manifests and key. To move the implementation checkout, first update the automation's
absolute script path. To resume after downtime, record the gap; never backdate fetched
content. For Package 2, import only verified raw records under the same provenance
contract, preserving failed and excluded observations in the denominator.
