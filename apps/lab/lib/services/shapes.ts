// LAB-10: the Lab's input shapes, defined once. They replace the UUID and USD regexes copied into
// auth/memberships.ts, review/index.ts, datasets/flows.ts, judge/core.ts, judge/runs.ts and pipelines/fake.ts.
// Two UUID strictnesses, deliberately: ids the Lab mints or forwards as record ids are lowercase v4
// (UUID_RE); ids it only reads back (memberships, a request under review) accept any version, either case.

/** A lowercase v4 UUID, unanchored, for composing refs (`lab:payer:${UUID}:...`). */
export const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
export const UUID_RE = new RegExp(`^${UUID}$`);
export const UUID_ANY_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** A Lab Amount value in PROVIDER_USD, exact to 1e-8 (R159). */
export const USD_RE = /^(0|[1-9][0-9]{0,11})\.[0-9]{8}$/;
