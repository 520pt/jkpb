(() => {
  if (!document.getElementById("patrolLogExportPanel")) return;
  const form = $("patrolLogQueryForm");
  const dateInput = $("patrolLogDate");
  const personInput = $("patrolLogPerson");
  const typeInput = $("patrolLogType");
  const body = $("patrolLogRows");
  const status = $("patrolLogStatus");
  const selectAll = $("patrolLogSelectAll");
  const exportButton = $("patrolLogExportBtn");
  const results = $("patrolLogResults");
  const davForm = $("patrolWebDAVForm");
  const davStatus = $("patrolWebDAVStatus");
  const davUpload = $("patrolLogWebDAVBtn");
  let davReady = false;
  let davBusy = false;
  let rows = [];
  let query = null;
  let selected = new Set();
  let busy = false;
  const today = Object.fromEntries(new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date()).map((part) => [part.type, part.value]));
  dateInput.value = today.year + "-" + today.month + "-" + today.day;
  dateInput.addEventListener("click", () => {
    if (typeof dateInput.showPicker !== "function") return;
    try {
      dateInput.showPicker();
    } catch (error) {
      void error;
    }
  });

  function updateSelection() {
    selectAll.disabled = busy || !rows.length;
    selectAll.checked = !!rows.length && selected.size === rows.length;
    selectAll.indeterminate = selected.size > 0 && selected.size !== rows.length;
    exportButton.disabled = busy || !query || !selected.size;
    davUpload.disabled = busy || davBusy || !davReady || !query || !selected.size;
    exportButton.textContent = "导出选中（" + selected.size + "）";
  }
  function setBusy(value) {
    busy = value;
    form.querySelectorAll("input, select, button").forEach((control) => { control.disabled = value; });
    results.querySelectorAll("input, button").forEach((control) => { control.disabled = value; });
    form.setAttribute("aria-busy", String(value));
    updateDAVControls();
    updateSelection();
  }
  function render() {
    body.innerHTML = rows.length ? rows.map((row) => {
      const id = escapeHtml(row.id);
      const checkbox = '<td><label class="patrol-log-check"><input type="checkbox" data-patrol-log-select="' +
        id + '"' + (selected.has(row.id) ? " checked" : "") + ' aria-label="选择该条巡查日志" /></label></td>';
      const cells = ["date", "category", "section", "person_names", "start_time", "end_time"]
        .map((key) => "<td>" + escapeHtml(row[key] || "-") + "</td>").join("");
      return "<tr>" + checkbox + cells + '<td><button type="button" data-patrol-log-download="' +
        id + '">下载 Excel</button></td></tr>';
    }).join("") : '<tr><td colspan="8">暂无查询结果</td></tr>';
    updateSelection();
  }
  function clearResults() {
    rows = [];
    query = null;
    selected.clear();
    render();
    status.textContent = "筛选条件已更改，请重新查询。";
  }
  [dateInput, personInput, typeInput].forEach((input) => input.addEventListener("change", clearResults));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    const filters = { target: dateInput.value, person_name: personInput.value.trim(), patrol_type: typeInput.value };
    rows = [];
    query = null;
    selected.clear();
    render();
    setBusy(true);
    status.textContent = "正在查询智慧养护平台，请稍候…";
    try {
      const response = await fetch("/api/patrol-logs?" + new URLSearchParams(filters));
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "查询失败，请稍后重试。");
      if (!Array.isArray(data.rows)) throw new Error("查询结果格式异常，请重试。");
      rows = data.rows.map((row) => ({ ...row, id: String(row.id) }));
      query = filters;
      selected = new Set(rows.map((row) => row.id));
      render();
      const groups = new Map();
      rows.forEach((row) => {
        const key = row.category + " · " + row.section;
        groups.set(key, (groups.get(key) || 0) + 1);
      });
      status.textContent = rows.length
        ? "共 " + rows.length + " 条：" + Array.from(groups, ([key, count]) => key + " " + count + " 条").join("；")
        : "该日期未查询到符合条件的巡查日志。";
    } catch (error) {
      status.textContent = "查询失败：" + (error.message || error);
    } finally {
      setBusy(false);
    }
  });
  selectAll.addEventListener("change", () => {
    selected = selectAll.checked ? new Set(rows.map((row) => row.id)) : new Set();
    body.querySelectorAll("[data-patrol-log-select]").forEach((input) => { input.checked = selectAll.checked; });
    updateSelection();
  });
  body.addEventListener("change", (event) => {
    const input = event.target.closest("[data-patrol-log-select]");
    if (!input || busy) return;
    if (input.checked) selected.add(input.dataset.patrolLogSelect);
    else selected.delete(input.dataset.patrolLogSelect);
    updateSelection();
  });
  async function exportLogs(ids) {
    if (busy || !query || !ids.length) return;
    if (!window.confirm("将先检查选中的 " + ids.length + " 条巡查日志是否已导出；未导出过将直接下载，是否继续？")) {
      status.textContent = "已取消导出。";
      return;
    }
    setBusy(true);
    status.textContent = "正在检查导出历史，请稍候…";
    try {
      const payload = JSON.stringify({ ...query, record_ids: ids });
      const check = await fetch("/api/patrol-logs/export-status", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: payload,
      });
      const history = await check.json().catch(() => ({}));
      if (!check.ok) throw new Error(typeof history.detail === "string" ? history.detail : "检查导出历史失败，请稍后重试。");
      if (!Array.isArray(history.exported) || !Array.isArray(history.unexported) || history.total !== ids.length ||
          history.exported.length + history.unexported.length !== ids.length) throw new Error("导出历史结果异常，已停止下载，请重新查询。");
      if (history.exported.length) {
        const filenames = history.exported.slice(0, 5).map((row) => row.filename).join("\n");
        const more = history.exported.length > 5 ? "\n……" : "";
        if (!window.confirm("所选日志中有 " + history.exported.length + " 条已经导出过：\n" + filenames + more +
            "\n另有 " + history.unexported.length + " 条未导出。是否仍要导出全部选中日志？")) {
          status.textContent = "已取消导出，未发起下载。";
          return;
        }
      }
      status.textContent = "正在核对并下载原格式巡查记录，请稍候…";
      const response = await fetch("/api/patrol-logs/export", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: payload,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : "导出失败，请稍后重试。");
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const encoded = disposition.split("filename*=UTF-8''")[1];
      const fallback = "巡查日志_" + query.target + (ids.length === 1 ? ".xlsx" : ".zip");
      const filename = encoded ? decodeURIComponent(encoded.split(";")[0]) : fallback;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.textContent = "已生成 " + ids.length + " 条巡查日志的下载，请查看浏览器下载列表。";
    } catch (error) {
      status.textContent = "导出失败：" + (error.message || error);
    } finally {
      setBusy(false);
    }
  }
  exportButton.addEventListener("click", () => exportLogs(Array.from(selected)));
  body.addEventListener("click", (event) => {
    const button = event.target.closest("[data-patrol-log-download]");
    if (button) exportLogs([button.dataset.patrolLogDownload]);
  });
  const davBrowseDialog = $("patrolWebDAVBrowserDialog");
  const davPreviewDialog = $("patrolWebDAVPreviewDialog");
  const davDirectoryLabel = $("patrolWebDAVDirectoryLabel");
  const davBrowserPath = $("patrolWebDAVBrowserPath");
  const davBrowserStatus = $("patrolWebDAVBrowserStatus");
  const davDirectoryList = $("patrolWebDAVDirectoryList");
  const davPreviewBase = $("patrolWebDAVPreviewBase");
  const davPreviewTree = $("patrolWebDAVPreviewTree");
  const davPreviewStatus = $("patrolWebDAVPreviewStatus");
  const davConfirm = $("patrolWebDAVConfirmBtn");
  let davBrowsePath = "";
  let davSelectedPath = null;
  let davPlan = null;

  function updateDAVControls() {
    davForm.querySelectorAll("input, button").forEach((control) => { control.disabled = busy || davBusy; });
    davForm.setAttribute("aria-busy", String(busy || davBusy));
    davUpload.disabled = busy || davBusy || !davReady || !query || !selected.size;
  }
  function setDAVBusy(value) {
    davBusy = value;
    updateDAVControls();
    updateSelection();
  }
  function setDAVPath(path) {
    davSelectedPath = path;
    $("patrolWebDAVDirectory").value = davSelectedPath || "";
    davDirectoryLabel.textContent = davSelectedPath === null ? "尚未选择真实文件夹。" : "已选择真实文件夹：/" + davSelectedPath;
  }
  function applyDAVConfig(config) {
    $("patrolWebDAVUrl").value = config.base_url || "";
    $("patrolWebDAVUsername").value = config.username || "";
    $("patrolWebDAVPassword").value = "";
    $("patrolWebDAVPassword").placeholder = config.password_configured ? "已配置，留空保留原密码" : "请输入 WebDAV 密码";
    setDAVPath(config.base_url ? (config.directory || "") : null);
    davReady = !!config.base_url;
    updateSelection();
  }
  function davPayload(path) {
    return {
      base_url: $("patrolWebDAVUrl").value.trim(),
      username: $("patrolWebDAVUsername").value.trim(),
      password: $("patrolWebDAVPassword").value,
      directory: path === undefined ? davSelectedPath : path,
    };
  }
  async function davJSON(path, options) {
    const response = await fetch("/api/patrol-logs/webdav/" + path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "WebDAV 请求失败，请稍后重试。");
    return data;
  }
  function renderDAVDirectory(data) {
    davBrowsePath = data.path || "";
    davBrowserPath.textContent = davBrowsePath ? "/" + davBrowsePath : "/（WebDAV 根目录）";
    davDirectoryList.replaceChildren();
    const directories = data.directories || [];
    directories.forEach((entry) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "进入文件夹：" + entry.name;
      button.dataset.webdavPath = entry.path;
      davDirectoryList.appendChild(button);
    });
    if (!directories.length) {
      const empty = document.createElement("p");
      empty.className = "webdav-browser-empty";
      empty.textContent = "当前文件夹没有子文件夹，可以选择当前文件夹。";
      davDirectoryList.appendChild(empty);
    }
    if ((data.files || []).length) {
      const files = document.createElement("p");
      files.className = "webdav-browser-empty";
      files.textContent = "当前文件夹已有 " + data.files.length + " 个文件，仅显示文件夹供选择。";
      davDirectoryList.appendChild(files);
    }
    $("patrolWebDAVParentBtn").disabled = !davBrowsePath;
    $("patrolWebDAVChooseBtn").disabled = false;
    $("patrolWebDAVRefreshBtn").disabled = false;
    davBrowserStatus.textContent = "已从 WebDAV 实际读取当前目录，可以选择当前文件夹或进入子文件夹。";
  }
  async function browseDAV(path) {
    if (busy || davBusy || !davForm.reportValidity()) return;
    setDAVBusy(true);
    davBrowsePath = path;
    davBrowserPath.textContent = path ? "/" + path : "/（WebDAV 根目录）";
    davBrowserStatus.textContent = "正在读取真实远端目录…";
    davDirectoryList.replaceChildren();
    $("patrolWebDAVChooseBtn").disabled = true;
    $("patrolWebDAVParentBtn").disabled = true;
    $("patrolWebDAVRefreshBtn").disabled = true;
    if (!davBrowseDialog.open) davBrowseDialog.showModal();
    try {
      const data = await davJSON("directories", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(davPayload(path)),
      });
      renderDAVDirectory(data);
      if (!davBrowseDialog.open) davBrowseDialog.showModal();
    } catch (error) {
      davBrowserStatus.textContent = "读取失败：" + error.message;
      $("patrolWebDAVParentBtn").disabled = !davBrowsePath;
      $("patrolWebDAVRefreshBtn").disabled = false;
    } finally {
      setDAVBusy(false);
    }
  }
  async function loadDAVConfig() {
    setDAVBusy(true);
    try {
      const data = await davJSON("config");
      applyDAVConfig(data.config);
      davStatus.textContent = davReady ? "已读取已保存的真实 WebDAV 文件夹；如需更换，请重新浏览选择。" : "尚未选择 WebDAV 文件夹；不影响下载 Excel 或 ZIP。";
    } catch (error) {
      davStatus.textContent = "读取配置失败：" + error.message;
    } finally {
      setDAVBusy(false);
    }
  }
  async function saveDAVConfig() {
    if (busy || davBusy || davSelectedPath === null || !davForm.reportValidity()) {
      if (davSelectedPath === null) davStatus.textContent = "请先点击“选择目录”，选择一个实际存在的文件夹。";
      return false;
    }
    setDAVBusy(true);
    davStatus.textContent = "正在核验并保存真实 WebDAV 文件夹…";
    try {
      const data = await davJSON("config", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(davPayload()),
      });
      applyDAVConfig(data.config);
      davStatus.textContent = "WebDAV 文件夹已保存，上传前还会再次读取并预览年份、月份和分类目录。";
      return true;
    } catch (error) {
      davReady = false;
      davStatus.textContent = "保存失败：" + error.message;
      return false;
    } finally {
      setDAVBusy(false);
    }
  }
  $("patrolWebDAVBrowseBtn").addEventListener("click", () => browseDAV(davSelectedPath ?? ""));
  $("patrolWebDAVDirectoryList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-webdav-path]");
    if (button) browseDAV(button.dataset.webdavPath);
  });
  $("patrolWebDAVParentBtn").addEventListener("click", () => {
    const parts = davBrowsePath.split("/").filter(Boolean);
    parts.pop();
    browseDAV(parts.join("/"));
  });
  $("patrolWebDAVRefreshBtn").addEventListener("click", () => browseDAV(davBrowsePath));
  $("patrolWebDAVBrowserCancelBtn").addEventListener("click", () => davBrowseDialog.close());
  $("patrolWebDAVChooseBtn").addEventListener("click", () => {
    if (davBusy) return;
    setDAVPath(davBrowsePath);
    davReady = false;
    davStatus.textContent = "已选择真实文件夹，请先保存 WebDAV 配置。";
    davBrowseDialog.close();
    updateSelection();
  });
  $("patrolWebDAVBrowserDialog").addEventListener("click", (event) => {
    if (event.target === davBrowseDialog && !davBusy) davBrowseDialog.close();
  });
  davForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveDAVConfig();
  });
  davForm.addEventListener("input", (event) => {
    if (event.target.id === "patrolWebDAVDirectory") return;
    davReady = false;
    setDAVPath(null);
    davStatus.textContent = "WebDAV 地址或账号已修改，请重新读取并选择真实文件夹。";
    updateSelection();
  });
  $("patrolWebDAVTestBtn").addEventListener("click", async () => {
    if (!(await saveDAVConfig())) return;
    setDAVBusy(true);
    davStatus.textContent = "正在测试已选择的 WebDAV 文件夹…";
    try {
      await davJSON("test", { method: "POST" });
      davStatus.textContent = "WebDAV 文件夹连接成功，上传前仍会重新分析远端目录。";
    } catch (error) {
      davStatus.textContent = "连接测试失败：" + error.message;
    } finally {
      setDAVBusy(false);
    }
  });

  function renderDAVPlan(plan) {
    davPlan = plan;
    davPreviewBase.textContent = "保存根目录：/" + (plan.base_directory || "");
    davPreviewTree.replaceChildren();
    (plan.folders || []).forEach((folder) => {
      const row = document.createElement("div");
      row.className = "webdav-plan-row";
      row.textContent = "文件夹：" + folder.path + (folder.exists ? "（已存在）" : "（待新建）");
      davPreviewTree.appendChild(row);
    });
    (plan.files || []).forEach((file) => {
      const row = document.createElement("div");
      row.className = "webdav-plan-row" + (file.exists ? " webdav-conflict" : "");
      row.textContent = "文件：" + file.path + (file.exists ? "（已有同名文件，不能覆盖）" : "（待上传）");
      davPreviewTree.appendChild(row);
    });
    davPreviewStatus.textContent = plan.can_confirm
      ? "共 " + plan.records + " 条，目录结构检查完成，可以确认创建并保存。"
      : "发现已有同名文件或没有可保存记录，已禁止写入，请关闭后调整目录或重新查询。";
    davConfirm.disabled = !plan.can_confirm || !plan.plan_token;
  }
  async function previewDAV(ids) {
    if (busy || davBusy || !davReady || !query || !ids.length) return;
    setBusy(true);
    status.textContent = "正在读取真实 WebDAV 目录并生成保存预览…";
    try {
      const data = await davJSON("preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...query, record_ids: ids }),
      });
      renderDAVPlan(data);
      davPreviewDialog.showModal();
    } catch (error) {
      status.textContent = "保存预览失败：" + error.message;
    } finally {
      setBusy(false);
    }
  }
  davPreviewDialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
  $("patrolWebDAVPreviewCancelBtn").addEventListener("click", () => {
    if (!busy) { davPlan = null; davPreviewDialog.close(); }
  });
  $("patrolWebDAVConfirmBtn").addEventListener("click", async () => {
    if (busy || !davPlan || !davPlan.plan_token || !query || !selected.size) return;
    const ids = Array.from(selected);
    setBusy(true);
    davConfirm.disabled = true;
    davPreviewStatus.textContent = "正在再次核验目录并创建缺少的文件夹…";
    try {
      const data = await davJSON("export", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...query, record_ids: ids, plan_token: davPlan.plan_token }),
      });
      davPreviewDialog.close();
      status.textContent = "已保存 " + data.records + " 条巡查日志；新建文件夹 " + (data.created_directories || []).length + " 个。";
      davPlan = null;
    } catch (error) {
      davPreviewStatus.textContent = "保存失败：" + error.message + "，未继续覆盖文件。";
    } finally {
      setBusy(false);
    }
  });
  davUpload.addEventListener("click", () => previewDAV(Array.from(selected)));
  updateSelection();
  loadDAVConfig();
})();
