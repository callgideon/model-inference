/**
 * I2A: check the deployment environment once when a server starts. A refusal (lib/deploy/env.ts,
 * variable names only) stops the server from serving rather than failing on a user's request.
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { assertDeployEnv } = await import("./lib/deploy/env.ts");
  assertDeployEnv(process.env);
}
