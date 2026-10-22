import { createContext, useContext } from "react";
import type { RevealedFileItem } from "../../services/api";

export interface KnowledgeImportContextValue {
  /** Extensions the knowledge base accepts; empty/absent hides the menu item. */
  allowedExtensions: readonly string[];
  open: (file: RevealedFileItem) => void;
}

export const KnowledgeImportContext =
  createContext<KnowledgeImportContextValue | null>(null);

export function useKnowledgeImport(): KnowledgeImportContextValue | null {
  return useContext(KnowledgeImportContext);
}
