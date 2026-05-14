import { normalizeBaseUrl } from "./api.js";
import { loadSettings, saveSettings } from "./storage.js";

const backendInput = document.querySelector<HTMLInputElement>("#backend-url");
const saveButton = document.querySelector<HTMLButtonElement>("#save");
const status = document.querySelector<HTMLParagraphElement>("#status");

async function init(): Promise<void> {
  const settings = await loadSettings();
  if (backendInput) {
    backendInput.value = settings.backendUrl;
  }
}

saveButton?.addEventListener("click", async () => {
  const backendUrl = normalizeBaseUrl(backendInput?.value || "");
  await saveSettings({ backendUrl });
  if (status) {
    status.textContent = "Saved.";
  }
});

void init();
