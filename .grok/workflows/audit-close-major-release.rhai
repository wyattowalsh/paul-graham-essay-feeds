let meta = #{
    name: "audit-close-major-release",
    description: "Close the 2026-08-31 audit ledger and ready a coherent major release without tagging",
    when_to_use: "After the 2026-08-31 paul-graham-essay-feeds audit: implement remaining P0-P3 remediations, rematerialize the seven-file product, and produce a release-readiness report. Do not cut git tags or GitHub releases.",
    phases: [
        #{ title: "Inventory", detail: "confirm each finding is still open against current source" },
        #{ title: "Publication", detail: "lock inode race, generation id, POSIX classifier" },
        #{ title: "HTTP", detail: "final-hop 304 and unknown Content-Encoding" },
        #{ title: "Summaries", detail: "reject nav/promo text and carry provenance" },
        #{ title: "Catalog", detail: "absence hysteresis and fair cursor" },
        #{ title: "Pipeline", detail: "CAS, materiality, evidence, link-validation contract" },
        #{ title: "Settings", detail: "bounded defaults and --all-pages" },
        #{ title: "CI", detail: "true 90% coverage and candidate-to-product SHA chain" },
        #{ title: "Docs", detail: "subscribe-first README, NOTICE, SECURITY, CHANGELOG, version" },
        #{ title: "Product", detail: "repair seven bad summaries and rematerialize bundle" },
        #{ title: "Verify", detail: "ruff, ty, pytest, product check" },
        #{ title: "Repair", detail: "one fix wave if gates fail" },
        #{ title: "Report", detail: "release-readiness ledger" },
    ],
};

fn trimmed(s) {
    if type_of(s) == "string" { s.trim(); s } else { "" }
}

fn blob(r) {
    if r == () { return "null"; }
    if !r.success { return "{\"error\":\"agent-failed\"}"; }
    json_encode(r.output)
}

fn agent_ok(r) {
    r != () && r.success
}

fn notes_of(r) {
    if r == () { return "agent-slot-empty"; }
    if !r.success { return "agent-failed"; }
    if r.output == () { return ""; }
    if type_of(r.output) == "map" && r.output.notes != () { return trimmed(r.output.notes); }
    if type_of(r.output) == "string" { return trimmed(r.output); }
    ""
}

fn result_schema() {
    #{
        "type": "object",
        "properties": #{
            "ok": #{ "type": "boolean" },
            "changed": #{ "type": "boolean" },
            "findings_closed": #{
                "type": "array",
                "maxItems": 24,
                "items": #{ "type": "string" },
            },
            "tests_run": #{ "type": "string" },
            "notes": #{ "type": "string" },
        },
    }
}

fn inventory_schema() {
    #{
        "type": "object",
        "properties": #{
            "cluster": #{ "type": "string" },
            "open_ids": #{
                "type": "array",
                "maxItems": 16,
                "items": #{ "type": "string" },
            },
            "closed_ids": #{
                "type": "array",
                "maxItems": 16,
                "items": #{ "type": "string" },
            },
            "notes": #{ "type": "string" },
        },
    }
}

fn verify_schema() {
    #{
        "type": "object",
        "properties": #{
            "ok": #{ "type": "boolean" },
            "format_ok": #{ "type": "boolean" },
            "lint_ok": #{ "type": "boolean" },
            "types_ok": #{ "type": "boolean" },
            "tests_ok": #{ "type": "boolean" },
            "check_ok": #{ "type": "boolean" },
            "coverage_note": #{ "type": "string" },
            "notes": #{ "type": "string" },
        },
    }
}

fn standing() {
    let s = "Repo: /Users/ww/dev/projects/paul-graham-essay-feeds. ";
    s += "Read Agents.md, DOCS.md architecture decisions, and the current files you will change before editing. ";
    s += "Product: metadata-only RSS/Atom/JSON Feed; catalog.json is SSOT; six flat feeds/ files; no site/, no OPML, no full essay bodies, no invented day-1 dates, no LLM summaries, no docs/ tree. ";
    s += "Python 3.12+ / uv. Absolute imports. Pydantic extra=forbid on durable models; every Field has a description; aware UTC. ";
    s += "CLI remains update + check only. Quiet success is zero stdout+stderr. ";
    s += "Do not git commit, tag, push, branch, or create a GitHub release. ";
    s += "Do not add compatibility shims unless persisted catalog/feed bytes require them. ";
    s += "Write or extend regression tests for each closure criterion. Run the focused pytest files you touch with: uv run pytest <paths> -q. ";
    s += "Match existing code style. Keep comments short and factual. ";
    s += "Use read_file/grep/search_replace. Inspect code before answering. An empty change is valid only after you read the relevant source and tests.\n";
    s
}

fn audit_context() {
    let s = "Audit 2026-08-31 (PGF-2026-*) at implementation SHA c136c4976c42616946a5a5e156d86042ea4a52ec. ";
    s += "Skip code changes for accepted-risk PGF-2026-015 (raw GitHub MIME) and accepted-design-risk PGF-2026-018 (per-file materialize); document them only. ";
    s += "PGF-2026-016 is a GitHub ruleset: document operator steps in DOCS.md; do not apply rulesets. ";
    s += "Do not rewrite the project.\n";
    s
}

let target_version = "1.0.0";
if args != () && type_of(args) == "map" && args.target_version != () {
    if type_of(args.target_version) == "string" && trimmed(args.target_version) != "" {
        target_version = args.target_version;
        target_version.trim();
    } else {
        pause("verification", "args.target_version must be a nonempty string such as 1.0.0");
    }
}
log("target_version=" + target_version);

phase("Inventory");
let inv_jobs = [];
let inv_prompt = standing();
inv_prompt += audit_context();
inv_prompt += "Read-only. Use grep/read_file on the named surfaces. Return open_ids still reproducing and closed_ids already fixed. Do not edit.\n";

let p0 = inv_prompt;
p0 += "Cluster publication: PGF-2026-001 unlink-before-unlock in src/paul_graham_essay_feeds/publication.py release_write_lock; PGF-2026-003 last_generation_id allocated after catalog_to_json in write_staging_generation; PGF-2026-006 module-level fcntl vs OS Independent in pyproject.toml. Confirm whether tests/characterization/audit/test_aud_002_lock.py covers the three-actor inode race.";
inv_jobs.push(#{
    prompt: p0,
    label: "inventory:publication",
    capability_mode: "read-only",
    output_schema: inventory_schema(),
});

let phttp = inv_prompt;
phttp += "Cluster HTTP: PGF-2026-011 _hop_exchange sets extra_headers=None on redirect then request_with_evidence evaluates 304 against original headers; PGF-2026-017 _decode_content_encoding continues past unknown tokens; PGF-2026-010 ResourceState has byte counts/encoding but pipeline _apply_enrichment omits them on 200 and can drop them on 304.";
inv_jobs.push(#{
    prompt: phttp,
    label: "inventory:http",
    capability_mode: "read-only",
    output_schema: inventory_schema(),
});

let psum = inv_prompt;
psum += "Cluster summaries: PGF-2026-022 seven enriched summaries are translation/nav/promo. Catalog prior_good is equally bad. pipeline._summary_quality hard-codes 0.9. enrich.score_summary_quality exists but adapter/pipeline drop flags. List the seven URLs from catalog.json.";
inv_jobs.push(#{
    prompt: psum,
    label: "inventory:summaries",
    capability_mode: "read-only",
    output_schema: inventory_schema(),
});

let ppipe = inv_prompt;
ppipe += "Cluster pipeline/catalog/settings: PGF-2026-002 no base-digest CAS under lock; PGF-2026-005 validate_links skipped on skip_network; PGF-2026-008 page_fetch_cursor advances by served count not last index; PGF-2026-009 material_catalog_digest includes page_raw_sha256; PGF-2026-013 reconcile hard-deletes 1-4 absences; PGF-2026-014 max_page_fetches default None.";
inv_jobs.push(#{
    prompt: ppipe,
    label: "inventory:pipeline",
    capability_mode: "read-only",
    output_schema: inventory_schema(),
});

let inv = parallel(inv_jobs);
log("inventory notes: " + notes_of(inv[0]) + " | " + notes_of(inv[1]) + " | " + notes_of(inv[2]) + " | " + notes_of(inv[3]));

phase("Publication");
let pub_prompt = standing();
pub_prompt += audit_context();
pub_prompt += "Implement PGF-2026-001, PGF-2026-003, PGF-2026-006 in this repo.\n";
pub_prompt += "PGF-2026-001: Never unlink .cache/write.lock during normal release. Keep one stable inode; only unlock and close the fd. Token payload may still be rewritten by the new owner. Add a deterministic three-actor multiprocess regression: B opens inode X; A unlinks-or-would-unlink and unlocks X; C creates/locks inode Y; B locks X — two exclusive owners must be impossible. Put it in tests/characterization/audit/test_aud_002_lock.py and/or tests/unit/test_publication_lock.py. Update tests that currently expect unlink-on-release.\n";
pub_prompt += "PGF-2026-003: In write_staging_generation allocate gen_id first, stamp catalog.last_generation_id, then catalog_to_json, then write artifacts + MANIFEST. Manifest, pointer, and public catalog must agree. Newly published schema-v2/v3 bundles must have a non-null last_generation_id. Update unit tests around staging.\n";
pub_prompt += "PGF-2026-006: Do not invent a Windows lock. Narrow pyproject classifiers from OS Independent to POSIX (MacOS/POSIX). Document POSIX-only flock in DOCS.md briefly. Wheel/import contract must match advertised OS.\n";
pub_prompt += "Do not change HTTP, enrich, or pipeline CAS here except catalog stamp inside write_staging_generation. Run the publication/lock tests when done.";
let pub = agent(pub_prompt, #{
    label: "fix:publication",
    capability_mode: "all",
    output_schema: result_schema(),
});
log("publication: " + notes_of(pub));

phase("HTTP");
let http_prompt = standing();
http_prompt += audit_context();
http_prompt += "Implement PGF-2026-011 and PGF-2026-017 in src/paul_graham_essay_feeds/http.py plus tests.\n";
http_prompt += "PGF-2026-011: Redirect hops currently set extra_headers=None so the final hop may be unconditional while 304 acceptance still inspects the original request headers. Record the headers actually sent on the final hop. An unconditional final-hop 304 must never be ResultKind.NOT_MODIFIED. Keep AUD-016: 304 is NOT_MODIFIED only with conditionals actually sent and prior material.\n";
http_prompt += "PGF-2026-017: Fail closed on every unsupported declared non-identity Content-Encoding token. Do not treat unknown encodings as identity. Persist decoding source where evidence already has a field; add tests for gzip/deflate/br order and unknown token failure. Do not add a runtime brotli dependency; existing missing-brotli error stays.\n";
http_prompt += "Do not edit publication.py. Run http characterization/unit tests.";
let http_fix = agent(http_prompt, #{
    label: "fix:http",
    capability_mode: "all",
    output_schema: result_schema(),
});
log("http: " + notes_of(http_fix));

phase("Summaries");
let sum_prompt = standing();
sum_prompt += audit_context();
sum_prompt += "Implement PGF-2026-022 extraction/quality (not yet the committed catalog rewrite).\n";
sum_prompt += "Reject translation menus, high-link-density navigation, known YC/book promos, related-link blocks, domain-search chrome. Prefer the actual essay paragraph. Carry summary source, score, and flags end-to-end from enrich through PageMetadata/Essay into pipeline storage — stop hard-coding source page and quality 0.9.\n";
sum_prompt += "Below threshold, fall back to prior-good only when prior-good itself passes the semantic gate; otherwise deterministic title-based simple summary. Promo/navigation-only candidates must fail semantic verification in verify.py.\n";
sum_prompt += "Add characterization tests for all seven known cases (minimized HTML fixtures, not live): Before the Startup; Organic Startup Ideas; Why to Not Not Start a Startup; The 18 Mistakes That Kill Startups; Ideas for Startups; How to Start a Startup; How to Make Wealth.\n";
sum_prompt += "Touch enrich.py, models if fields are missing, verify.py, tests/characterization/. Leave catalog.json/feeds rewrite to the later Product phase. Run the new tests.";
let sum_fix = agent(sum_prompt, #{
    label: "fix:summaries",
    capability_mode: "all",
    output_schema: result_schema(),
});
log("summaries: " + notes_of(sum_fix));

phase("Catalog");
let cat_prompt = standing();
cat_prompt += audit_context();
cat_prompt += "Implement PGF-2026-008 cursor persistence helpers and PGF-2026-013 absence hysteresis in catalog.py / discover.py / models.py.\n";
cat_prompt += "PGF-2026-008: Fair rotation must persist (last_selected_index+1) % catalog_size and advance after attempts including failures while preserving backoff. Do not advance only by served work count over the due subset. Pipeline will call the helper in the next phase — export a clear function.\n";
cat_prompt += "PGF-2026-013: Track consecutive absence privately. A one-run omission of 1-4 items must not publish deletions. A second consistent observation may hard-delete (current-index mirror). Do not add public tombstones or soft-retain feed states. Keep the >=5 removal-ratio quarantine. Default 0 fields must load from existing catalogs (extra=forbid).\n";
cat_prompt += "Do not rewrite pipeline.py except tiny imports if unavoidable. Run catalog/discover tests.";
let cat_fix = agent(cat_prompt, #{
    label: "fix:catalog",
    capability_mode: "all",
    output_schema: result_schema(),
});
log("catalog: " + notes_of(cat_fix));

phase("Pipeline");
let pipe_prompt = standing();
pipe_prompt += audit_context();
pipe_prompt += "Implement PGF-2026-002, PGF-2026-005, PGF-2026-008 (caller), PGF-2026-009, PGF-2026-010 in pipeline.py and related tests.\n";
pipe_prompt += "PGF-2026-002: Attach the durable catalog material digest (post-fix definition) on which the candidate was based. Under the writer lock, if current durable state differs, reject stale finalize — rebase/re-plan or abort with a clear FeedError. A slower older candidate must not publish over a newer accepted state. Add a barrier test.\n";
pipe_prompt += "PGF-2026-005: validate_links=true must not be skipped by the ordinary no-op/skip_network path. Run link validation as an independent planned phase even when enrich/page fetches are skipped. Expose checked/skipped counts on the machine result (PipelineResult / CLI --result-file). Align settings Field text with the actual contract.\n";
pipe_prompt += "PGF-2026-008: Use the catalog helper so the persisted cursor is last selected index + 1 modulo size, including failed attempts.\n";
pipe_prompt += "PGF-2026-009: material_catalog_digest must not include wire/raw hashes. Keep raw hashes as provenance-only. Materiality is decoded content plus rendered/feed-visible fields (title, url, summary*, published_*, order, decoded hash).\n";
pipe_prompt += "PGF-2026-010: On accepted 200 persist raw/decoded hashes, byte counts, and selected_encoding. On 304 preserve prior hashes/counts/encoding while advancing clocks. Carry decoding provenance from the page adapter.\n";
pipe_prompt += "Do not unlink the lock file. Do not restage generation id allocation (already in publication.py). Run pipeline unit/integration tests.";
let pipe_fix = agent(pipe_prompt, #{
    label: "fix:pipeline",
    capability_mode: "all",
    output_schema: result_schema(),
});
log("pipeline: " + notes_of(pipe_fix));

phase("Settings");
let set_prompt = standing();
set_prompt += audit_context();
set_prompt += "Implement PGF-2026-014 in settings.py, cli.py, notebook.ipynb, and tests.\n";
set_prompt += "Default max_page_fetches to a conservative bound matching CI (40), not unlimited. Default max_link_validations similarly if still None. Full-corpus refresh requires explicit opt-in: add --all-pages (and env if needed) that sets the cap to None. Print planned request counts before network work when not quiet.\n";
set_prompt += "Colab notebook must not launch an uncapped crawl; default bounded/due-only. Do not change the scheduled workflow caps except to keep them explicit.\n";
set_prompt += "Update unit tests that assumed None defaults. Run settings/cli tests.";
let set_fix = agent(set_prompt, #{
    label: "fix:settings",
    capability_mode: "all",
    output_schema: result_schema(),
});
log("settings: " + notes_of(set_fix));

phase("CI");
let ci_prompt = standing();
ci_prompt += audit_context();
ci_prompt += "Implement PGF-2026-007 and PGF-2026-012, plus safer privileged cache defaults (audit item 19).\n";
ci_prompt += "PGF-2026-007: coverage precision must be at least 2. 89.955% must fail; 90.000% must pass. Do not rely on precision=1 rounding. Set [tool.coverage.report] precision and fail_under accordingly. In .github/workflows/ci.yml, after pytest XML, fail the job if covered/valid from coverage.xml is < 0.90 using the raw totals (lines+branches). Add a unit/fixture proving the ratio helper rejects 89.955%.\n";
ci_prompt += "PGF-2026-012: Bind one candidate artifact to one final product SHA. Exact uploaded workspace must gate publish (verify the downloaded seven files, not a sibling checkout of the source SHA). After commit/push, emit product_sha=$(git rev-parse HEAD). Re-check that exact tree. Attest subjects with provenance context naming source SHA, candidate digest, seven subjects, and product SHA. verify-product.yml must check that product SHA (artifact or explicit ref), not mutable main HEAD. Bot push still uses force-with-lease.\n";
ci_prompt += "setup-uv: stop forcing enable-cache: true on privileged publish/verify-product/release jobs; keep safer defaults or explicitly disable cache there.\n";
ci_prompt += "Do not weaken existing SHA-pinned actions. Run any new coverage-ratio tests.";
let ci_fix = agent(ci_prompt, #{
    label: "fix:ci",
    capability_mode: "all",
    output_schema: result_schema(),
});
log("ci: " + notes_of(ci_fix));

phase("Docs");
let docs_prompt = standing();
docs_prompt += audit_context();
docs_prompt += "Implement PGF-2026-004 (docs/version coherence, no git tag), PGF-2026-019, PGF-2026-020, PGF-2026-021, and document 015/016/018.\n";
docs_prompt += "Set package version to " + target_version + " in src/paul_graham_essay_feeds/__init__.py. CHANGELOG: keep historical [0.2.0] as the prior advertised-but-untagged integrity work; add [" + target_version + "] Unreleased-to-ready section listing PGF-2026-001..022 closures. README and notebook must not pin a tag that does not exist — pin the upcoming " + target_version + " as the intended release and show git+https install from main until the tag exists, with one maintained sentence.\n";
docs_prompt += "PGF-2026-021: Lead README with one-click Subscribe links for the six raw GitHub feeds. Recommend simple feeds first; enriched second now that semantic gating exists. Move Colab under maintainer/custom-generation. A reader must subscribe without Python.\n";
docs_prompt += "PGF-2026-020: Add NOTICE (or LICENSE-adjacent scope in README+NOTICE): software MIT; upstream essay text and derived titles/summaries remain Paul Graham's / original rights. Do not relicense third-party text.\n";
docs_prompt += "PGF-2026-019: Add concise SECURITY.md and CONTRIBUTING.md at repo root. Optional CODEOWNERS for Wyatt if it matches repo practice.\n";
docs_prompt += "DOCS.md: document lock (stable inode), generation id stamp, CAS, coverage precision, candidate→product SHA chain, local seven-file visibility (018), raw GitHub MIME (015), and the exact gh ruleset steps for requiring CI/PRs with a bot path bypass (016) — do not apply them.\n";
docs_prompt += "Fold maintainer guidance into DOCS.md only. Run no live network.";
let docs_fix = agent(docs_prompt, #{
    label: "fix:docs",
    capability_mode: "all",
    output_schema: result_schema(),
});
log("docs: " + notes_of(docs_fix));

phase("Product");
let prod_prompt = standing();
prod_prompt += audit_context();
prod_prompt += "Rematerialize the committed seven-file product so it matches the new code contracts. No live paulgraham.com crawl.\n";
prod_prompt += "Repair the seven known-bad catalog summaries. prior_good is equally bad, so use the deterministic title-based simple summary (or a passing extractor output if a local fixture exists). Clear bogus 0.9 quality; store real provenance/quality. URLs: before.html, organic.html, notnot.html, startupmistakes.html, startupideas.html, start.html, wealth.html — confirm paths from catalog.json.\n";
prod_prompt += "Rebuild all six feeds from the catalog via catalog_to_feed_snapshot + render_snapshot_feeds (enriched and simple). Publish through write_staging_generation + materialize_generation so last_generation_id is stamped and MANIFEST agrees. Then uv run pg-essay-feeds check --quiet must pass. Identity/order bijection across 234 items must hold.\n";
prod_prompt += "Do not bump logical timestamps for generator-only metadata unless content/summaries changed (the seven will change). Run check plus a focused verify/characterization slice.";
let prod_fix = agent(prod_prompt, #{
    label: "fix:product",
    capability_mode: "all",
    output_schema: result_schema(),
});
log("product: " + notes_of(prod_fix));

phase("Verify");
let ver_prompt = standing();
ver_prompt += "Run the local quality gates and report honestly from command output. Do not edit unless a command cannot start (missing uv) — in that case say so.\n";
ver_prompt += "Commands, in order, from repo root:\n";
ver_prompt += "1) uv run ruff format --check .\n";
ver_prompt += "2) uv run ruff check .\n";
ver_prompt += "3) uv run ty check\n";
ver_prompt += "4) uv run pytest --cov-fail-under=90\n";
ver_prompt += "5) uv run pg-essay-feeds check --quiet\n";
ver_prompt += "Set ok true only if all five succeed. Include coverage_note with the reported percent. Quote failing command tails in notes.";
let verified = agent(ver_prompt, #{
    label: "verify:gates",
    capability_mode: "all",
    output_schema: verify_schema(),
});
log("verify: " + notes_of(verified));

let gates_ok = false;
if agent_ok(verified) && verified.output != () && verified.output.ok == true {
    gates_ok = true;
}

if !gates_ok {
    phase("Repair");
    let repair_prompt = standing();
    repair_prompt += audit_context();
    repair_prompt += "Quality gates failed. Previous verify output (untrusted JSON):\n";
    repair_prompt += blob(verified);
    repair_prompt += "\nFix the smallest set of issues so ruff format/check, ty, pytest --cov-fail-under=90, and pg-essay-feeds check --quiet all pass. Do not git commit. Re-run the five commands yourself and report ok from those reruns.";
    let repaired = agent(repair_prompt, #{
        label: "repair:gates",
        capability_mode: "all",
        output_schema: verify_schema(),
    });
    log("repair: " + notes_of(repaired));
    if agent_ok(repaired) && repaired.output != () && repaired.output.ok == true {
        gates_ok = true;
        verified = repaired;
    }
}

phase("Report");
let report_prompt = standing();
report_prompt += "Write a release-readiness report as markdown. Do not edit product code unless you only need to read it.\n";
report_prompt += "Target version: " + target_version + ".\n";
report_prompt += "Gates ok: " + gates_ok.to_string() + ".\n";
report_prompt += "Inventory blobs: " + blob(inv[0]) + " :: " + blob(inv[1]) + " :: " + blob(inv[2]) + " :: " + blob(inv[3]) + "\n";
report_prompt += "Phase notes publication/http/summaries/catalog/pipeline/settings/ci/docs/product: ";
report_prompt += notes_of(pub) + " | " + notes_of(http_fix) + " | " + notes_of(sum_fix) + " | " + notes_of(cat_fix) + " | ";
report_prompt += notes_of(pipe_fix) + " | " + notes_of(set_fix) + " | " + notes_of(ci_fix) + " | " + notes_of(docs_fix) + " | " + notes_of(prod_fix) + "\n";
report_prompt += "Verify blob: " + blob(verified) + "\n";
report_prompt += "Produce markdown with: (1) executive verdict GO/NO-GO for cutting " + target_version + "; (2) table of PGF-2026-001 through 022 with status closed/deferred/accepted-risk and evidence path; (3) remaining human steps (git tag, GitHub release with wheel/sdist, ruleset apply, Colab pin after tag); (4) commands the maintainer should run. You may write the markdown via write_scratch conceptually in your output.notes field as the full report (notes can be long).";
let reported = agent(report_prompt, #{
    label: "report:readiness",
    capability_mode: "read-only",
    output_schema: result_schema(),
});

let report_text = notes_of(reported);
if report_text == "" { report_text = "Release-readiness report was empty."; }
let report_path = write_scratch_file("release-readiness.md", report_text);

complete(#{
    target_version: target_version,
    gates_ok: gates_ok,
    report_path: report_path,
    summary: "Audit-close workflow finished; gates_ok=" + gates_ok.to_string() + " target=" + target_version,
});
