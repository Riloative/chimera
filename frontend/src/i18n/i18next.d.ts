import "i18next";
import type { Resources } from "./resources";

// Typed keys: a misspelled key fails `tsc`.
declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "common";
    resources: Resources;
    returnNull: false;
  }
}
