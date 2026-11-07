import type { components } from "./schema";
export type Schema = components["schemas"];
export type Context = Schema["ContextOutput"];
export type Incident = Schema["IncidentOutput"];
export type Plan = Schema["PlanOutput"];
export type Analysis = Schema["AnalysisOutput"];
export type Job = Schema["JobOutput"];
export type WorkspaceData = Schema["IncidentWorkspaceOutput"];
export type User = {
  user_id: string;
  username: string;
  role: "DISPATCHER" | "DRIVER";
  vehicle_id: string | null;
};
export type LoginResult = {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
};
export type Envelope<T> = {
  code: string;
  message: string;
  data: T | null;
  error: Schema["ErrorDetail"] | null;
  request_id: string;
  operation_id: string | null;
  meta: Schema["Meta"];
};
