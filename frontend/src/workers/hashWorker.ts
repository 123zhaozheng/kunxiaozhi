/**
 * Web Worker for computing SHA-256 hash of a file.
 *
 * crypto.subtle only exists in secure contexts (HTTPS / localhost); on
 * plain-HTTP intranet deployments it is undefined, so fall back to the
 * pure-JS implementation (identical digest output).
 */
import { sha256Hex } from "./sha256";

self.onmessage = async (e: MessageEvent<{ file: File }>) => {
  const { file } = e.data;

  try {
    const buffer = await file.arrayBuffer();
    let hashHex: string;
    if (typeof crypto !== "undefined" && crypto.subtle) {
      const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
      hashHex = Array.from(new Uint8Array(hashBuffer))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
    } else {
      hashHex = sha256Hex(new Uint8Array(buffer));
    }

    self.postMessage({ hash: hashHex });
  } catch (error) {
    self.postMessage({
      error: error instanceof Error ? error.message : "Hash computation failed",
    });
  }
};
