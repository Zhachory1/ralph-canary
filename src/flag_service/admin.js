"use strict";

(() => {
  const tokenInput = document.querySelector("#token");
  const status = document.querySelector("#status");
  const flagsOutput = document.querySelector("#flags");
  const historyOutput = document.querySelector("#history");

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${tokenInput.value}`);
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers, credentials: "omit", cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(`${payload.code}: ${payload.message}`);
    return payload;
  }

  async function loadFlags() {
    try {
      const payload = await request("/v1/admin/flags");
      flagsOutput.textContent = JSON.stringify(payload, null, 2);
      status.textContent = `${payload.items.length} flags loaded.`;
    } catch (error) {
      status.textContent = error.message;
    }
  }

  document.querySelector("#refresh").addEventListener("click", loadFlags);
  document.querySelector("#clear-token").addEventListener("click", () => {
    tokenInput.value = "";
    status.textContent = "Token cleared from this page.";
  });

  document.querySelector("#create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    try {
      const result = await request("/v1/admin/flags", {
        method: "POST",
        body: JSON.stringify({ flag_key: fields.get("flag_key"), value: fields.get("value") === "true" }),
      });
      status.textContent = `Created ${result.flag_key} at version ${result.version}.`;
      await loadFlags();
    } catch (error) {
      status.textContent = error.message;
    }
  });

  document.querySelector("#update-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    try {
      const result = await request(`/v1/admin/flags/${encodeURIComponent(fields.get("flag_key"))}`, {
        method: "PUT",
        headers: { "If-Match": `"${fields.get("version")}"` },
        body: JSON.stringify({ value: fields.get("value") === "true" }),
      });
      status.textContent = `Updated ${result.flag_key} to version ${result.version}.`;
      await loadFlags();
    } catch (error) {
      status.textContent = error.message;
    }
  });

  document.querySelector("#history-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const key = new FormData(event.currentTarget).get("flag_key");
    try {
      const payload = await request(`/v1/admin/flags/${encodeURIComponent(key)}/history`);
      historyOutput.textContent = JSON.stringify(payload, null, 2);
      status.textContent = `${payload.items.length} history records loaded.`;
    } catch (error) {
      status.textContent = error.message;
    }
  });
})();
