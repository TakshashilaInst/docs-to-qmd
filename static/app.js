/* Takshashila QMD Converter — frontend logic */

(function () {
  "use strict";

  const form        = document.getElementById("convertForm");
  const convertBtn  = document.getElementById("convertBtn");
  const btnLabel    = convertBtn.querySelector(".btn-label");
  const spinner     = convertBtn.querySelector(".spinner");

  const resultArea  = document.getElementById("resultArea");
  const successCard = document.getElementById("successCard");
  const errorCard   = document.getElementById("errorCard");
  const resultMsg   = document.getElementById("resultMsg");
  const errorMsg    = document.getElementById("errorMsg");

  const dlZip = document.getElementById("dlZip");
  const dlQmd = document.getElementById("dlQmd");
  const dlPdf = document.getElementById("dlPdf");

  // Prefill today's date
  const dateInput = document.getElementById("date");
  if (!dateInput.value) {
    dateInput.value = new Date().toISOString().slice(0, 10);
  }

  // ── Auto-generate filename from title + date ───────────────────────────────
  const titleInput    = document.getElementById("title");
  const fnInput       = document.getElementById("pdf_filename");
  let fnUserEdited    = false;

  function makeFilename(title, date) {
    const datePart = (date || "").replace(/-/g, "") ||
      new Date().toISOString().slice(0, 10).replace(/-/g, "");
    let slug = title.toLowerCase()
      .replace(/[^a-z0-9\s-]/g, "")
      .trim()
      .replace(/\s+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "");
    if (slug.length > 45) {
      slug = slug.slice(0, 45).replace(/-[^-]*$/, "");
    }
    return `${datePart}-${slug || "document"}`;
  }

  function updateFilename() {
    if (fnUserEdited) return;
    const title = titleInput.value.trim();
    const date  = dateInput.value.trim();
    if (title) {
      fnInput.value = makeFilename(title, date);
    }
  }

  titleInput.addEventListener("input", updateFilename);
  dateInput.addEventListener("change", updateFilename);
  fnInput.addEventListener("input", () => { fnUserEdited = fnInput.value !== ""; });

  // Trigger once on load if title is pre-filled
  updateFilename();

  // ── Collect category checkboxes into hidden field ──────────────────────────
  function syncCategories() {
    const checked = [...document.querySelectorAll('input[name="categories_cb"]:checked')]
      .map(cb => cb.value);
    document.getElementById("categories").value = checked.join(", ");
  }
  document.querySelectorAll('input[name="categories_cb"]').forEach(cb => {
    cb.addEventListener("change", syncCategories);
  });

  // ── Form submit ────────────────────────────────────────────────────────────
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!validateForm()) return;

    syncCategories();
    setLoading(true);
    hideResults();

    const formData = new FormData(form);
    // Checkbox: if unchecked, FormData won't include it — add explicit bool
    formData.set("render_pdf", document.getElementById("render_pdf").checked ? "true" : "false");
    // Remove the checkbox group; the hidden field already has the value
    formData.delete("categories_cb");

    let zipBlob, zipWarning, zipFilename;
    try {
      const resp = await fetch("/api/convert", {
        method: "POST",
        body: formData,
      });

      zipWarning  = resp.headers.get("X-Typst-Warning");
      zipFilename = resp.headers.get("X-Filename") || fnInput.value.trim() || "output";

      if (!resp.ok) {
        let detail = `Server error (${resp.status})`;
        try {
          const json = await resp.json();
          detail = json.detail || detail;
        } catch (_) {}
        throw new Error(detail);
      }

      zipBlob = await resp.blob();
    } catch (err) {
      showError(err.message);
      setLoading(false);
      return;
    }

    setLoading(false);
    showSuccess(zipBlob, zipWarning, zipFilename);
  });

  // ── Validation ─────────────────────────────────────────────────────────────
  function validateForm() {
    let ok = true;
    ["google_doc_url", "title", "authors", "date"].forEach((id) => {
      const el = document.getElementById(id);
      if (!el.value.trim()) {
        el.classList.add("error");
        ok = false;
      } else {
        el.classList.remove("error");
      }
    });

    const urlEl = document.getElementById("google_doc_url");
    if (urlEl.value.trim() && !urlEl.value.includes("docs.google.com") && !urlEl.value.includes("drive.google.com")) {
      urlEl.classList.add("error");
      ok = false;
      alert("Please paste a Google Docs URL (docs.google.com or drive.google.com).");
    }

    const fnEl = document.getElementById("pdf_filename");
    if (fnEl.value.trim() && !/^[A-Za-z0-9_\-]+$/.test(fnEl.value.trim())) {
      fnEl.classList.add("error");
      ok = false;
      alert("Filename may only contain letters, numbers, hyphens and underscores.");
    }

    return ok;
  }

  // Clear error styling on input
  document.querySelectorAll("input, textarea").forEach((el) => {
    el.addEventListener("input", () => el.classList.remove("error"));
  });

  // ── UI helpers ─────────────────────────────────────────────────────────────
  function setLoading(on) {
    convertBtn.disabled = on;
    btnLabel.textContent = on ? "Converting…" : "Convert";
    spinner.hidden = !on;
  }

  function hideResults() {
    resultArea.hidden = true;
    successCard.hidden = true;
    errorCard.hidden = true;
  }

  function showSuccess(zipBlob, warning, stem) {
    const zipUrl = URL.createObjectURL(zipBlob);

    dlZip.href = zipUrl;
    dlZip.download = `${stem}.zip`;

    dlQmd.href = zipUrl;
    dlQmd.download = `${stem}.zip`;
    dlQmd.textContent = "Download .zip (QMD + images)";

    const hasPdf = document.getElementById("render_pdf").checked && !warning;
    dlPdf.hidden = !hasPdf;
    if (hasPdf) {
      dlPdf.href = zipUrl;
      dlPdf.download = `${stem}.zip`;
      dlPdf.textContent = "Download .zip (includes PDF)";
    }

    let msg = `Your .qmd and rendered PDF are packaged in <strong>${stem}.zip</strong>. Unzip it and upload the folder to Google Drive alongside your project.`;
    if (warning) {
      msg += `<br><em style="color:#b45309">⚠ ${warning}</em>`;
    }
    resultMsg.innerHTML = msg;

    resultArea.hidden = false;
    successCard.hidden = false;
    resultArea.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function showError(msg) {
    errorMsg.textContent = msg;
    resultArea.hidden = false;
    errorCard.hidden = false;
    resultArea.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // "Try again" / "Convert another"
  document.getElementById("tryAgainBtn").addEventListener("click", hideResults);
  document.getElementById("convertAnother").addEventListener("click", () => {
    hideResults();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
})();
