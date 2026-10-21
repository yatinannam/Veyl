/** The seeded demo people (mirrors backend/app/demo/content.py). Shown on the sign-in page
 * only when the deployment publishes the demo password. */
export interface DemoAccount {
  workspace: string;
  email: string;
  name: string;
  role: string;
  sees: string;
}

export const DEMO_ACCOUNTS: readonly DemoAccount[] = [
  { workspace: "acme", email: "alice@acme.test", name: "Alice", role: "Admin, Acme", sees: "Everything at Acme, including the board financials" },
  { workspace: "acme", email: "mark@acme.test", name: "Mark", role: "Manager, Acme", sees: "Confidential plans and his own review, not the financials" },
  { workspace: "acme", email: "mia@acme.test", name: "Mia", role: "Member, Acme", sees: "Internal documents only" },
  { workspace: "globex", email: "gina@globex.test", name: "Gina", role: "Admin, Globex", sees: "Globex only, never Acme" },
  { workspace: "globex", email: "gary@globex.test", name: "Gary", role: "Member, Globex", sees: "Nothing restricted, nothing from Acme" },
];
