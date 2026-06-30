/** Operator identity shown in the shell user menu (auth wiring TBD). */

export interface OperatorProfile {
  name: string;
  email: string;
}

const STORAGE_KEY = "factory.dashboard.operator";

const DEFAULT_PROFILE: OperatorProfile = {
  name: "Opérateur Factory",
  email: "operator@roxabi.dev",
};

export function readOperatorProfile(): OperatorProfile {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PROFILE;
    const parsed = JSON.parse(raw) as Partial<OperatorProfile>;
    if (typeof parsed.name === "string" && typeof parsed.email === "string") {
      return { name: parsed.name, email: parsed.email };
    }
  } catch {
    // private mode / corrupt storage
  }
  return DEFAULT_PROFILE;
}

export function persistOperatorProfile(profile: OperatorProfile): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
  } catch {
    // ignore
  }
}
