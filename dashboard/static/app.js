(() => {
  const stateOrder = ["CORRUPTING", "DETECTED", "QUARANTINED", "REPAIRING", "REVALIDATING", "PUBLISHING", "HEALTHY"];
  const el = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  let snapshotData = null;
  let ageChart = null;
  let driftChart = null;

  const prettyTime = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  };
  const ratioLabel = (value) => value == null || Number.isNaN(Number(value)) ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
  const countLabel = (value) => value == null || value === "—" || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString();
  const safeMetric = (metricSet, name) => {
    if (!metricSet || typeof metricSet !== "object") return "Waiting for pipeline artifact";
    const flat = metricSet.metrics && typeof metricSet.metrics === "object" ? metricSet.metrics : metricSet;
    if (typeof flat.retrieval_hit_rate === "number" && typeof flat.mean_token_f1 === "number") {
      const judge = typeof flat.judge_accuracy === "number" ? ` · Judge ${(flat.judge_accuracy * 100).toFixed(0)}%` : "";
      return `Hit ${(flat.retrieval_hit_rate * 100).toFixed(0)}% · Token F1 ${flat.mean_token_f1.toFixed(2)}${judge}`;
    }
    const keys = Object.keys(flat);
    if (!keys.length) return "Metrics artifact available";
    return keys.slice(0, 3).map((key) => `${key}: ${typeof flat[key] === "number" ? flat[key].toFixed(3) : String(flat[key])}`).join(" · ");
  };

  function setStatus(element, status, labels = {}) {
    element.className = `status-chip ${status}`;
    element.textContent = labels[status] || status.toUpperCase();
  }

  function renderKpis(data) {
    const healthLabel = { healthy: "Healthy", failed: "Repair failed", running: "Recovering", waiting: "Waiting" }[data.health] || "Waiting";
    el("pipelineHealth").textContent = healthLabel;
    el("healthDot").className = `status-dot ${data.health}`;
    el("healthDetail").textContent = data.last_run ? `${data.latest_state} · ${prettyTime(data.last_run.updated_at)}` : "No self-healing run yet";
    const qualityStatus = data.quality?.status || "waiting";
    el("qualityValue").textContent = qualityStatus === "passed" ? "Passed" : qualityStatus === "failed" ? "Failed" : "Waiting";
    el("qualitySub").textContent = data.quality?.gx_status === "waiting" ? "GX adapter waiting · core gate active" : data.quality?.gx_status === "passed" ? "Core + GX checks available" : "GX checks reported failures";
    const freshness = data.freshness || {};
    el("freshnessValue").textContent = freshness.stale_ratio == null ? "—" : ratioLabel(freshness.stale_ratio);
    el("freshnessSub").textContent = freshness.total_rows ? `${countLabel(freshness.stale_rows)} stale of ${countLabel(freshness.total_rows)} papers` : "Waiting for clean dataset";
    el("indexedDocuments").textContent = countLabel(data.active_documents);
    const activeName = data.last_run?.active_collection || "papers-baseline";
    el("activeIndexName").textContent = `${activeName} · active collection`;
    el("datasetCount").textContent = `${countLabel(data.datasets?.baseline?.rows)} records`;
    el("updatedAt").textContent = `Updated ${new Date(data.generated_at).toLocaleTimeString()}`;
    el("footerTime").textContent = prettyTime(data.generated_at);
    el("dateStamp").textContent = new Date(data.generated_at).toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
  }

  function renderComparison(data) {
    const qualityLabel = (key) => ({ passed: "Passed", failed: "Failed", waiting: "Waiting" })[data.dataset_quality?.[key]] || "Waiting";
    const states = [
      { key: "baseline", label: "Baseline", swatch: "baseline", quality: qualityLabel("baseline"), rows: data.datasets?.baseline?.rows, docs: data.datasets?.baseline?.documents },
      { key: "corrupted", label: "Corrupted", swatch: "corrupted", quality: qualityLabel("corrupted"), rows: data.datasets?.corrupted?.rows, docs: data.datasets?.corrupted?.documents },
      { key: "repaired", label: "Repaired", swatch: "repaired", quality: qualityLabel("repaired"), rows: data.datasets?.repaired?.rows, docs: data.datasets?.repaired?.documents },
    ];
    const body = states.map((row) => {
      const metricText = safeMetric(data.retrieval_metrics?.[row.key], row.key);
      const qualityClass = row.quality === "Passed" ? "passed" : row.quality === "Failed" ? "failed" : "waiting";
      return `<tr><td><span class="state-name"><i class="state-swatch ${row.swatch}"></i>${row.label}</span></td><td><span class="quality-pill ${qualityClass}">● ${row.quality}</span></td><td>${countLabel(row.rows)}</td><td>${countLabel(row.docs)}</td><td>${escapeHtml(metricText)}</td></tr>`;
    }).join("");
    el("comparisonBody").innerHTML = body;
  }

  function renderQuality(data) {
    const status = data.quality?.status || "waiting";
    setStatus(el("qualityChip"), status, { passed: "GATE PASSED", failed: "GATE FAILED", waiting: "AWAITING DATA" });
    const gxArtifacts = data.gx_artifacts || {};
    const gxSignals = Object.entries(gxArtifacts)
      .filter(([, report]) => report && typeof report === "object")
      .map(([name, report]) => `${name}: ${report.success === true ? "pass" : report.success === false ? "fail" : "available"}`);
    el("gxStatus").textContent = gxSignals.length
      ? gxSignals.join(" · ")
      : data.quality?.gx_status === "waiting" ? "Waiting for pipeline artifact" : data.quality?.gx_status || "Waiting for pipeline artifact";
    const checks = Array.isArray(data.quality?.checks) ? data.quality.checks : [];
    if (!checks.length) {
      el("expectationList").innerHTML = '<div class="waiting-block">Quality checks will appear when a report artifact is available.</div>';
      return;
    }
    el("expectationList").innerHTML = checks.map((check) => {
      const passed = Boolean(check.success);
      let observed = check.observed;
      if (observed && typeof observed === "object") {
        if (check.name === "schema") {
          observed = observed.missing?.length ? `Missing columns: ${observed.missing.join(", ")}` : `All ${observed.columns?.length || "required"} columns present`;
        } else {
          observed = Object.entries(observed).map(([key, value]) => `${key.replaceAll("_", " ")}: ${value}`).join(" · ");
        }
      }
      return `<div class="expectation-row"><div class="expectation-label">${escapeHtml(check.name || "Quality check")}<span class="expectation-detail">${escapeHtml(observed ?? check.expected ?? "")}</span></div><span class="check-status ${passed ? "" : "fail"}"><i class="check-icon">${passed ? "✓" : "!"}</i>${passed ? "PASS" : "FAIL"}</span></div>`;
    }).join("");
  }

  function renderState(data) {
    const current = data.latest_state || "IDLE";
    const activeIndex = stateOrder.indexOf(current);
    document.querySelectorAll(".state-step").forEach((step) => {
      const state = step.dataset.state;
      const index = stateOrder.indexOf(state);
      step.classList.toggle("done", activeIndex >= 0 && index < activeIndex);
      step.classList.toggle("current", index === activeIndex && data.health === "running");
      step.classList.toggle("failed", current === "REPAIR_FAILED" && index === stateOrder.indexOf("REVALIDATING"));
    });
    document.querySelectorAll(".state-flow > i").forEach((line, index) => line.classList.toggle("done", activeIndex > index + 1));
    const chipState = data.health === "healthy" ? "passed" : data.health === "failed" ? "failed" : data.health === "running" ? "running" : "neutral";
    setStatus(el("stateChip"), chipState, { passed: "HEALTHY", failed: "REPAIR FAILED", running: current, neutral: "IDLE" });

    const run = data.last_run;
    if (!run) {
      el("runSummary").innerHTML = '<div class="summary-empty">No recovery run in this session.</div>';
      el("repairSource").textContent = "Repair source · —";
    } else {
      const preCore = run.pre_repair_checks?.core || run.pre_repair_checks || {};
      const failedChecks = Array.isArray(preCore.checks) ? preCore.checks.filter((check) => !check.success).length : "—";
      el("runSummary").innerHTML = `<div class="summary-values"><div class="summary-value"><span>BASELINE ROWS</span><b>${countLabel(run.row_count_before)}</b></div><div class="summary-value"><span>CORRUPTED ROWS</span><b>${countLabel(run.row_count_corrupted)}</b></div><div class="summary-value"><span>FAILED CHECKS</span><b>${countLabel(failedChecks)}</b></div><div class="summary-value"><span>REPAIRED ROWS</span><b>${countLabel(run.row_count_after)}</b></div></div>`;
      el("repairSource").textContent = `Repair source · ${run.repair_source_used || run.repair_source_requested || "—"}`;
    }
    const active = data.health === "running";
    el("drillButton").disabled = active;
    el("drillButton").innerHTML = active ? "◌ Running failure drill…" : "<span>▶</span> Run failure drill";
  }

  function renderEvents(data) {
    const events = Array.isArray(data.events) ? [...data.events].reverse() : [];
    el("eventCount").textContent = `${events.length} events`;
    if (!events.length) {
      el("eventTimeline").innerHTML = '<div class="waiting-block">Events will stream here as the pipeline runs.</div>';
      return;
    }
    el("eventTimeline").innerHTML = events.map((event) => {
      const level = event.severity === "error" ? "error" : event.severity === "warning" ? "warning" : event.state === "HEALTHY" ? "success" : "";
      const failedChecks = event.payload?.failed_checks;
      const checkDetail = Array.isArray(failedChecks) && failedChecks.length ? ` · Failed checks: ${failedChecks.join(", ")}` : "";
      return `<div class="timeline-entry"><i class="timeline-dot ${level}"></i><div class="timeline-message">${escapeHtml(event.message || event.state)}<span class="timeline-meta">${escapeHtml(prettyTime(event.timestamp))} · ${escapeHtml(event.run_id || "pipeline")}${escapeHtml(checkDetail)}</span></div><b class="timeline-state">${escapeHtml(event.state || "EVENT")}</b></div>`;
    }).join("");
  }

  function renderAffected(data) {
    const ids = Array.isArray(data.affected_paper_ids) ? data.affected_paper_ids : [];
    el("affectedCount").textContent = `${ids.length} IDs`;
    el("affectedList").innerHTML = ids.length ? ids.map((id) => `<span class="affected-id">${escapeHtml(id)}</span>`).join("") : '<div class="waiting-block">No affected records in the latest run.</div>';
  }

  function renderArtifacts(data) {
    const artifacts = Array.isArray(data.artifacts) ? data.artifacts : [];
    el("artifactGrid").innerHTML = artifacts.map((artifact) => `<div class="artifact-item ${artifact.exists ? "exists" : ""}"><i class="artifact-indicator"></i><span><b>${escapeHtml(artifact.name)}</b><small>${escapeHtml(artifact.exists ? artifact.path : "Waiting for pipeline artifact")}</small></span></div>`).join("");
  }

  function renderCharts(data) {
    const ageNode = el("ageChart");
    const driftNode = el("driftChart");
    if (!window.echarts) {
      ageNode.innerHTML = '<div class="chart-fallback">Charts unavailable · CDN could not be reached</div>';
      driftNode.innerHTML = '<div class="chart-fallback">Charts unavailable · CDN could not be reached</div>';
      return;
    }
    if (!ageChart) {
      ageNode.innerHTML = "";
      ageChart = window.echarts.init(ageNode, null, { renderer: "canvas" });
    }
    const distribution = data.age_distribution || {};
    ageChart.setOption({
      animationDuration: 500,
      grid: { left: 34, right: 12, top: 14, bottom: 30 },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: { type: "category", data: Object.keys(distribution), axisLine: { lineStyle: { color: "#e3eaf1" } }, axisTick: { show: false }, axisLabel: { color: "#8c9cad", fontSize: 9, interval: 0 } },
      yAxis: { type: "value", minInterval: 1, splitLine: { lineStyle: { color: "#edf1f5", type: "dashed" } }, axisLabel: { color: "#9aa8b5", fontSize: 9 } },
      series: [{ type: "bar", barWidth: 26, data: Object.entries(distribution).map(([name, value], index) => ({ name, value, itemStyle: { color: ["#4b8dd4", "#38a6b0", "#49a99a", "#dfa74d"][index], borderRadius: [4, 4, 0, 0] } })), label: { show: true, position: "top", color: "#617b94", fontSize: 9 } }],
    }, true);

    const history = data.drift_history || [];
    if (!history.length) {
      driftNode.innerHTML = '<div class="chart-fallback">Waiting for run history</div>';
      driftChart?.dispose();
      driftChart = null;
      return;
    }
    if (!driftChart) {
      driftNode.innerHTML = "";
      driftChart = window.echarts.init(driftNode, null, { renderer: "canvas" });
    }
    const labels = history.map((item) => item.timestamp ? new Date(item.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "run");
    driftChart.setOption({
      animationDuration: 400,
      grid: { left: 38, right: 12, top: 16, bottom: 28 },
      tooltip: { trigger: "axis", valueFormatter: (value) => `${Number(value).toFixed(1)}%` },
      xAxis: { type: "category", data: labels, boundaryGap: false, axisLine: { lineStyle: { color: "#e3eaf1" } }, axisTick: { show: false }, axisLabel: { color: "#91a0af", fontSize: 8 } },
      yAxis: { type: "value", min: 0, max: 100, axisLabel: { color: "#9aa8b5", fontSize: 8, formatter: "{value}%" }, splitLine: { lineStyle: { color: "#edf1f5", type: "dashed" } } },
      series: [
        { name: "Before repair", type: "line", smooth: true, data: history.map((item) => item.stale_ratio_before == null ? null : Number(item.stale_ratio_before) * 100), symbolSize: 5, lineStyle: { width: 2, color: "#d9a24e" }, itemStyle: { color: "#d9a24e" }, areaStyle: { color: "rgba(217,162,78,.08)" }, connectNulls: true },
        { name: "After repair", type: "line", smooth: true, data: history.map((item) => item.stale_ratio_after == null ? null : Number(item.stale_ratio_after) * 100), symbolSize: 5, lineStyle: { width: 2, color: "#37a18a" }, itemStyle: { color: "#37a18a" }, connectNulls: true },
        { name: "SLA threshold", type: "line", data: history.map(() => 25), symbol: "none", lineStyle: { width: 1, color: "#cf7773", type: "dashed" }, tooltip: { show: false } },
      ],
      legend: { show: false },
    }, true);
  }

  function render(data) {
    snapshotData = data;
    renderKpis(data);
    renderComparison(data);
    renderQuality(data);
    renderState(data);
    renderEvents(data);
    renderAffected(data);
    renderArtifacts(data);
    renderCharts(data);
  }

  async function refresh() {
    try {
      const response = await fetch("/api/dashboard/snapshot", { cache: "no-store" });
      if (!response.ok) throw new Error(`Snapshot returned ${response.status}`);
      render(await response.json());
    } catch (error) {
      el("updatedAt").textContent = "Connection unavailable";
      el("healthDetail").textContent = error.message;
    }
  }

  async function startDrill() {
    const button = el("drillButton");
    button.disabled = true;
    el("drillFeedback").textContent = "Starting the quality failure drill…";
    try {
      const response = await fetch("/api/failure-drill", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trigger: "HAKI dashboard failure drill" }) });
      if (response.status === 409) throw new Error("A failure drill is already running. Follow its live events above.");
      if (!response.ok) throw new Error(`Could not start the drill (HTTP ${response.status}).`);
      const payload = await response.json();
      el("drillFeedback").textContent = `Run ${payload.run_id.slice(0, 12)} started. Quality detection and repair are automatic.`;
      await refresh();
    } catch (error) {
      el("drillFeedback").textContent = error.message;
      button.disabled = false;
    }
  }

  el("refreshButton").addEventListener("click", refresh);
  el("drillButton").addEventListener("click", startDrill);
  window.addEventListener("resize", () => { ageChart?.resize(); driftChart?.resize(); });

  refresh();
  const stream = new EventSource("/api/dashboard/events");
  stream.addEventListener("pipeline", (event) => {
    try {
      const data = JSON.parse(event.data);
      refresh();
    } catch (_) { /* Ignore malformed events and keep the stream alive. */ }
  });
  stream.addEventListener("artifact", refresh);
  stream.onerror = () => { document.querySelector(".connection-indicator").classList.add("offline"); };
  stream.onopen = () => { document.querySelector(".connection-indicator").classList.remove("offline"); };
  setInterval(refresh, 12000);
})();
