/**
 * dupe-sweep
 *
 * Finds exact-duplicate files -- proven by a SHA-256 match, never a
 * filename or size guess -- and safely quarantines every copy but one.
 * Dry run by default. Zero credentials, zero network, pure python3
 * standard library.
 *
 * @rote-frontmatter
 * ---
 * name: dupe-sweep
 * source: https://github.com/Satianurag/dupe-sweep
 * description: "Your Downloads folder has the same invoice three times and you didn't put them there -- a browser re-download, a cloud-sync conflict copy, an export run twice. This finds files that are byte-for-byte identical -- proven by a SHA-256 hash match after a cheap size pre-filter, never a filename or size guess -- and quarantines every copy but one, safely. Dry run by default: the first run touches nothing and shows the exact plan, with the exact number of bytes it would reclaim, not an estimate. Pass apply=true to actually move duplicates into a dated quarantine folder, never delete them -- a manifest travels with every run and an undo.py script ships inside the quarantine folder itself, so restoring needs no Rote install, just python3. Two files with identical content and different names are duplicates; two files with the same name and different content are never treated as one, no matter how similar their size. A hardlinked pair is detected and reported separately as already sharing storage -- deleting one would free zero bytes, so it is never counted as reclaimable and never moved. Zero-byte files are excluded outright: hashing them is a tautology and quarantining one reclaims nothing. Never descends into macOS app bundles (.app/.framework/.bundle/.photoslibrary and friends) or .git/node_modules -- those are opaque leaves, because deduplicating a file inside one can silently break an application or a repository, and this play's job is freeing clutter, not taking that risk. A file that changes size or mtime between the initial scan and the moment it would be hashed or moved is skipped with a stated reason, never acted on with stale data. A file that cannot be read (permission denied, vanished mid-scan) is reported, never silently dropped and never counted as clear. The keeper in each set is chosen deterministically -- oldest file first, then by which scanned path was listed first, then shortest path, then alphabetically -- so the same input always produces the same keeper, never a coin flip. Pass demo=true to run against bundled fixtures (including a real hardlinked pair) in an isolated temp copy, so apply=true is safe to try with zero setup and zero risk to anything real. Which copy is kept is controllable: keep_rule=oldest (default), newest, or priority (prefer a designated priority_paths folder as the source of truth, falling back to oldest -- stated, never silent -- when nothing matches), matching the 'keep newest'/'by priority folder' rules documented by real duplicate-finder tools. exclude_paths skips named directories or files entirely, pruned during the walk itself rather than filtered after -- a verified real want, not a guess: fdupes' own issue tracker carries a long-standing user request for exactly this."
 * provenance:
 *   author: Satianurag <anuragsati6476@gmail.com>
 *   workspace: satianurag/dupe-sweep
 * metadata:
 *   version: 0.2.0
 *   rote_version: 0.79.0
 *   status: released
 *   kind: atomic
 *   flow_type: parallel
 *   execution_model: steps_with_presentation
 *   format: typescript
 *   requires_endpoints: []
 *   requires_sessions: false
 *   contract:
 *     atomic: true
 *     input:
 *       type: none
 *     output:
 *       format: json
 *       destination: stdout
 *     composable: true
 *   discoverability:
 *     tags:
 *     - typescript
 *     - files
 *     - duplicates
 *     - disk-space
 *     - local-first
 *     - zero-credentials
 *     - deterministic
 *     - reversible
 *     - effect-write
 * parameters:
 * - name: paths
 *   param_type: string
 *   required: false
 *   default: "~/Downloads"
 *   description: "Comma-separated directories to scan (absolute, or ~-relative). Ignored in demo mode."
 *   example: "~/Downloads,~/Desktop"
 * - name: min_size_bytes
 *   param_type: integer
 *   required: false
 *   default: "4096"
 *   description: "Files smaller than this are ignored entirely -- most small files (icons, empty markers) are noise, not clutter worth reporting."
 *   example: "4096"
 * - name: max_files
 *   param_type: integer
 *   required: false
 *   default: "50000"
 *   description: "Scan budget guard. A scan that hits this cap stops and reports capped=true rather than silently truncating without saying so."
 *   example: "50000"
 * - name: include_hidden
 *   param_type: boolean
 *   required: false
 *   default: "false"
 *   description: "Include dotfiles and dot-directories in the scan. Off by default -- most hidden files are app/tool state, not user clutter."
 *   example: "false"
 * - name: exclude_paths
 *   param_type: string
 *   required: false
 *   default: ""
 *   description: "Comma-separated directories (or individual files) to skip entirely -- pruned during the walk itself, never even stat()'d. A verified real-world need: fdupes' own issue tracker has a long-standing user request for exactly this (an --exclude flag)."
 *   example: "~/Downloads/Archives,~/Downloads/DoNotTouch"
 * - name: keep_rule
 *   param_type: string
 *   required: false
 *   default: "oldest"
 *   description: "Which copy in each duplicate set is kept: oldest (default -- earliest mtime), newest (latest mtime), or priority (prefer a file under priority_paths, in the order listed; a set with no match falls back to oldest). Matches the 'keep newest'/'by priority folder' rules real duplicate-finder tools (Duplicate Cleaner Pro, Nektony) document. keep_rule=priority with priority_paths left empty degrades to oldest -- a stated fallback, never a silent one."
 *   example: "priority"
 * - name: priority_paths
 *   param_type: string
 *   required: false
 *   default: ""
 *   description: "Comma-separated directories treated as the source of truth when keep_rule=priority -- earlier in the list wins. Ignored for every other keep_rule."
 *   example: "~/Pictures/Library"
 * - name: apply
 *   param_type: boolean
 *   required: false
 *   default: "false"
 *   description: "Actually quarantine duplicates. Off by default: a run with apply=false (or omitted) only reports the plan and the exact reclaimable bytes, and touches nothing."
 *   example: "true"
 * - name: quarantine_dir
 *   param_type: string
 *   required: false
 *   default: "~/.rote/dupe-sweep/quarantine"
 *   description: "Where quarantined duplicates go, in a dated subfolder with their own manifest.json and undo.py. Never deleted outright."
 *   example: "~/.rote/dupe-sweep/quarantine"
 * - name: demo
 *   param_type: boolean
 *   required: false
 *   default: "false"
 *   description: "Run against bundled fixtures -- copied into an isolated temp directory first, so apply=true is safe to try with zero setup and zero risk to anything real (including a real hardlinked pair, to show that path is never treated as reclaimable)."
 *   example: "true"
 * tags:
 * - files
 * - duplicates
 * - disk-space
 * - local-first
 * - zero-credentials
 * - deterministic
 * - reversible
 * discoverability:
 *   tags:
 *   - files
 *   - duplicates
 *   - disk-space
 *   - local-first
 *   - zero-credentials
 *   - deterministic
 *   - reversible
 * write_permissions:
 * - resource: "<quarantine_dir>/<run-timestamp>/"
 *   mode: create
 *   why: "Duplicate files are moved here (shutil.move), never deleted. Each run gets its own dated subfolder holding a manifest.json (original path, quarantined path, sha256, size, keeper) and a copy of undo.py, so restoring needs only python3, no Rote install."
 * steps:
 *   discover:
 *     type: process.exec
 *     timeout_ms: 30000
 *     argv:
 *     - python3
 *     - '@resource{scripts/discover.py}'
 *     - $paths
 *     - $min_size_bytes
 *     - $max_files
 *     - $include_hidden
 *     - $demo
 *     - $exclude_paths
 *   scan:
 *     type: process.exec
 *     timeout_ms: 60000
 *     depends_on:
 *     - discover
 *     argv:
 *     - python3
 *     - '@resource{scripts/scan.py}'
 *     - '@discover{$.stdout.text | fromjson}'
 *     - $keep_rule
 *     - $priority_paths
 *   apply:
 *     type: process.exec
 *     timeout_ms: 60000
 *     depends_on:
 *     - scan
 *     execution:
 *       mode: deferred
 *       condition:
 *         compare:
 *           left:
 *             param: apply
 *           op: eq
 *           right: true
 *     argv:
 *     - python3
 *     - '@resource{scripts/apply.py}'
 *     - '@scan{$.stdout.text | fromjson}'
 *     - $quarantine_dir
 * presentation_fixtures:
 *   discover: resources/presentation-fixtures/discover/fixture.yaml
 *   scan: resources/presentation-fixtures/scan/fixture.yaml
 *   apply: resources/presentation-fixtures/apply/fixture.yaml
 * ---
 *
 * Usage:
 *   rote play run dupe-sweep demo=true
 *   rote play run dupe-sweep demo=true apply=true
 *   rote play run dupe-sweep paths=~/Downloads
 *   rote play run dupe-sweep paths=~/Downloads,~/Desktop apply=true
 *   rote play run dupe-sweep --output=json
 *
 * Output modes (the runner's --output flag):
 *   human (default)  duplicate sets, exact reclaimable bytes, next step
 *   summary          "3 sets · 14.8 MB reclaimable · dry run"
 *   json             the canonical dupe-sweep/1 result object
 */

// ---------------------------------------------------------------------------
// Presentation plane: deprivileged, renders only what the steps produced.
// Imports ONLY the presentation SDK; owns no effects, no fs, no fetch.
// ---------------------------------------------------------------------------

const {
  FlowOutput,
  isProcessExecBody,
  loadPresentationContext,
  stepName,
} = await import("__ROTE_PRESENTATION_SDK__");

const out = new FlowOutput();
const ctx = await loadPresentationContext();

interface SkipRecord {
  path: string;
  reason: string;
}

interface DuplicateSet {
  sha256: string;
  size: number;
  keep: string;
  duplicates: string[];
  reclaimable_bytes: number;
}

interface LinkedSet {
  sha256: string;
  size: number;
  paths: string[];
}

interface ManifestEntry {
  original_path: string;
  quarantined_path: string;
  sha256: string;
  size: number;
  kept_path: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function execStepStdout(
  label: string,
  step: ReturnType<typeof ctx.step>,
  available: ReturnType<typeof ctx.requireAvailable>,
): Record<string, unknown> {
  const status = step.outcome.status;
  if (status === "failed") {
    const output = asRecord(step.outcome.output);
    throw new Error(`step ${label} failed: ${String(output.message ?? "no message captured")}`);
  }
  if (status === "blocked") {
    throw new Error(`step ${label} was blocked: check upstream step failures`);
  }
  if (!isProcessExecBody(available.body)) {
    throw new Error(`step ${label} did not record a process.exec observation`);
  }
  const exit = available.body.status.exit;
  if (exit.kind !== "code" || exit.code !== 0) {
    throw new Error(
      `step ${label} exited ${exit.kind !== "code" ? exit.kind : exit.code}: ${
        available.body.stderr?.text ?? "no stderr captured"
      }`,
    );
  }
  const text = available.body.stdout?.text;
  if (text === undefined) {
    throw new Error(`step ${label} captured no stdout`);
  }
  try {
    return asRecord(JSON.parse(text));
  } catch (error) {
    throw new Error(`step ${label} stdout is not JSON: ${String(error)}`);
  }
}

function readDiscoverStdout(): Record<string, unknown> {
  return execStepStdout("discover", ctx.step(stepName("discover")), ctx.requireAvailable(stepName("discover")));
}

function readScanStdout(): Record<string, unknown> {
  return execStepStdout("scan", ctx.step(stepName("scan")), ctx.requireAvailable(stepName("scan")));
}

function readApplyStdout(): Record<string, unknown> {
  return execStepStdout("apply", ctx.step(stepName("apply")), ctx.requireAvailable(stepName("apply")));
}

function failedStepMessage(label: string, step: ReturnType<typeof ctx.step>): string | null {
  if (step.outcome.status !== "failed") return null;
  const output = asRecord(step.outcome.output);
  return `${label}: ${String(output.message ?? "no message captured")}`;
}

function boolParam(name: string, fallback: boolean): boolean {
  const value = ctx.params[name];
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    if (["true", "1", "yes"].includes(value.trim().toLowerCase())) return true;
    if (["false", "0", "no", ""].includes(value.trim().toLowerCase())) return false;
  }
  if (value === undefined || value === null) return fallback;
  throw new Error(`parameter ${name} must be a boolean, got ${String(value)}`);
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = n / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(value < 10 ? 2 : 1)} ${units[i]}`;
}

async function renderFailureViews(message: string): Promise<void> {
  const runFailed = ctx.run.status === "failed";
  const failedSteps = [
    failedStepMessage("discover", ctx.step(stepName("discover"))),
    failedStepMessage("scan", ctx.step(stepName("scan"))),
    failedStepMessage("apply", ctx.step(stepName("apply"))),
  ].filter((entry): entry is string => entry !== null);
  const detail = failedSteps.length > 0 ? failedSteps.join("; ") : message;
  out.human([
    "dupe-sweep · run failed",
    "",
    "  " + detail,
    "",
    runFailed
      ? "  Completed steps restore with --resume; dependent steps were blocked."
      : "  See the step stderr above for the actionable diagnostic.",
  ].join("\n"));
  out.summary("run failed — " + (failedSteps.length > 0 ? failedSteps[0].split(":")[0] : "see the human view"));
  out.result({
    schema: "dupe-sweep/1",
    error: message,
    run_status: ctx.run.status,
    run_id: ctx.run.run_id,
    representations: {
      human: "failure evidence — the failing step and its diagnostic",
      json: "canonical — error, run_status, run_id",
      summary: "intentionally lossy — failure signal only",
    },
  });
}

async function renderSuccess(): Promise<void> {
  const demo = boolParam("demo", false);
  const applyRequested = boolParam("apply", false);

  const discoverOut = readDiscoverStdout();
  const scanOut = readScanStdout();

  const rootsResolved = (discoverOut.roots_resolved as string[]) ?? [];
  const filesScanned = Number(scanOut.files_scanned ?? 0);
  const dupSets = (scanOut.duplicate_sets as DuplicateSet[]) ?? [];
  const linkedSets = (scanOut.linked_sets as LinkedSet[]) ?? [];
  const skips = (scanOut.skips as SkipRecord[]) ?? [];
  const totalReclaimable = Number(scanOut.total_reclaimable_bytes ?? 0);
  const totalDupFiles = Number(scanOut.total_duplicate_files ?? 0);
  const totalDupSets = Number(scanOut.total_duplicate_sets ?? dupSets.length);
  const previewCapped = Boolean(scanOut.preview_capped);
  const capped = Boolean(discoverOut.capped);
  const excludedCount = Number(discoverOut.excluded_count ?? 0);
  const keepRuleRequested = String(scanOut.keep_rule_requested ?? "oldest");
  const keepRuleEffective = String(scanOut.keep_rule_effective ?? "oldest");

  const applyStep = ctx.step(stepName("apply"));
  const applyRan =
    applyStep.outcome.status === "completed" || applyStep.outcome.status === "restored";

  let applyOut: Record<string, unknown> | null = null;
  if (applyRan) {
    applyOut = readApplyStdout();
  }

  const badge = demo ? " [DEMO]" : "";
  const lines: string[] = [];

  if (rootsResolved.length === 0 && !demo) {
    out.human([
      "dupe-sweep · no directories to scan",
      "",
      "  No valid paths were given, so nothing was checked — this is UNKNOWN, not clean.",
      "",
      "  Point it at a real folder:",
      "    rote play run satianurag/dupe-sweep paths=~/Downloads",
      "  Or see it work on bundled fixtures first:",
      "    rote play run satianurag/dupe-sweep demo=true",
    ].join("\n"));
    out.summary("UNKNOWN · no directories scanned");
    out.result({
      schema: "dupe-sweep/1",
      scanned: false,
      verdict: "UNKNOWN",
      roots_resolved: [],
      representations: {
        human: "complete — why nothing was scanned and the two ways forward",
        json: "canonical — verdict is UNKNOWN because zero directories were scanned",
        summary: "intentionally lossy — UNKNOWN verdict only",
      },
    });
    return;
  }

  const rootsLabel = rootsResolved.length <= 2
    ? rootsResolved.join(", ")
    : `${rootsResolved.slice(0, 2).join(", ")} +${rootsResolved.length - 2} more`;

  lines.push(`dupe-sweep · ${filesScanned} file(s) scanned · ${rootsLabel}${badge}`);
  lines.push("");

  if (totalDupSets === 0) {
    lines.push("  CLEAR — no exact duplicates found.");
  } else {
    for (const s of dupSets) {
      lines.push(`  FOUND    ${formatBytes(s.size)} × ${s.duplicates.length + 1} copies`);
      lines.push(`           keep: ${s.keep}`);
      for (const d of s.duplicates) {
        lines.push(`           dup:  ${d}`);
      }
    }
  }

  if (linkedSets.length > 0) {
    lines.push("");
    lines.push(`  already linked (not reclaimable — same storage, ${linkedSets.length} set(s)):`);
    for (const l of linkedSets) {
      lines.push(`    · ${formatBytes(l.size)} shared by ${l.paths.length} paths: ${l.paths.join(", ")}`);
    }
  }

  if (skips.length > 0) {
    lines.push("");
    lines.push(`  UNKNOWN (${skips.length}) — not counted, not guessed:`);
    for (const s of skips.slice(0, 5)) {
      lines.push(`    · ${s.path} — ${s.reason}`);
    }
    if (skips.length > 5) {
      lines.push(`    · ${skips.length - 5} more in the JSON result`);
    }
  }

  if (capped) {
    lines.push("");
    lines.push("  note: scan hit max_files and stopped — raise max_files for a complete scan.");
  }
  if (previewCapped) {
    lines.push("");
    lines.push(`  note: showing the top ${dupSets.length} of ${totalDupSets} duplicate set(s) by reclaimable size — apply=true still acts on all of them.`);
  }
  if (keepRuleRequested !== keepRuleEffective) {
    lines.push("");
    lines.push(`  note: keep_rule=${keepRuleRequested} requested with no priority_paths given — fell back to keep_rule=${keepRuleEffective}.`);
  }
  if (excludedCount > 0) {
    lines.push("");
    lines.push(`  excluded: ${excludedCount} path(s) under exclude_paths, never scanned.`);
  }

  lines.push("");

  if (applyRan && applyOut) {
    const moved = Number(applyOut.moved ?? 0);
    const failures = (applyOut.failures as SkipRecord[]) ?? [];
    const quarantineDir = applyOut.quarantine_dir as string | null;
    lines.push(`  quarantined ${moved} file(s)${quarantineDir ? ` → ${quarantineDir}` : ""}`);
    if (failures.length > 0) {
      lines.push(`  ${failures.length} could not be moved (reasons in JSON) — left in place, not lost.`);
    }
    if (moved > 0 && quarantineDir) {
      lines.push(`  undo: python3 ${quarantineDir}/undo.py`);
    }
  } else if (totalDupSets > 0) {
    lines.push(`  DRY RUN — ${formatBytes(totalReclaimable)} reclaimable across ${totalDupFiles} file(s).`);
    lines.push("  Nothing was touched. Re-run with apply=true to quarantine duplicates (reversible, never deleted).");
  }

  out.human(lines.join("\n"));

  const summaryVerdict = totalDupSets === 0
    ? "CLEAR"
    : applyRan
      ? `quarantined ${applyOut ? Number(applyOut.moved ?? 0) : 0} file(s)`
      : `${totalDupSets} set(s) · ${formatBytes(totalReclaimable)} reclaimable · dry run`;
  out.summary(`${summaryVerdict}${badge}`);

  out.result({
    schema: "dupe-sweep/1",
    scanned: true,
    files_scanned: filesScanned,
    roots_resolved: rootsResolved,
    demo,
    capped,
    excluded_count: excludedCount,
    keep_rule_requested: keepRuleRequested,
    keep_rule_effective: keepRuleEffective,
    duplicate_sets: dupSets,
    total_duplicate_sets: totalDupSets,
    preview_capped: previewCapped,
    linked_sets: linkedSets,
    skips,
    total_reclaimable_bytes: totalReclaimable,
    total_duplicate_files: totalDupFiles,
    applied: applyRan,
    apply_requested: applyRequested,
    apply_result: applyOut,
    play_version: "0.2.0",
    run_id: ctx.run.run_id,
    representations: {
      human: "complete — duplicate sets, linked (non-reclaimable) sets, unknowns, and either the dry-run byte count or what was quarantined",
      json: "canonical — top duplicate/linked/skip entries (by reclaimable size, capped at 150 for extremely large scans — preview_capped/total_duplicate_sets say when and how many) plus apply_result when apply=true; run_id is volatile",
      summary: "intentionally lossy — verdict and one headline number only",
    },
  });
}

try {
  if (ctx.run.status === "failed") {
    await renderFailureViews("the run recorded a failed step; inspect the stage ledger above");
  } else {
    await renderSuccess();
  }
} catch (error) {
  await renderFailureViews(String((error as Error)?.message ?? error));
}
