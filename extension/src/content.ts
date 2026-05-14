import { collectPageContext } from "./pageContext.js";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "studylens:get-page-context") {
    return false;
  }
  sendResponse({ type: "studylens:page-context", payload: collectPageContext() });
  return true;
});
