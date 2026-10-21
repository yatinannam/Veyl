import { ApiError } from "./api";

/** Plain-language explanations of API errors: what happened and what to do next. */
export function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something went wrong. Try again.";
  switch (error.code) {
    case "invalid_credentials":
      return "That workspace, email, and password don't match.";
    case "rate_limited":
      return "Too many attempts. Wait a few minutes, then try again.";
    case "llm_unavailable":
      return "The answer service is unavailable right now. Try again in a moment.";
    case "service_unavailable":
      return "Veyl can't reach one of its services right now. Try again in a moment.";
    case "forbidden":
      return "You don't have permission to do that.";
    case "not_found":
      return "That item doesn't exist or you no longer have access to it.";
    case "payload_too_large":
      return "That file is larger than the upload limit.";
    case "csrf_failed":
    case "unauthenticated":
      return "Your session has ended. Sign in again.";
    case "starting_up":
      return "Veyl is starting up, this can take a minute. Try again shortly.";
    case "document_limit_reached":
      return "This workspace has reached its document limit. Delete a document first.";
    case "network_error":
      return "Veyl can't be reached. Check your connection and try again.";
    default:
      return error.message;
  }
}
