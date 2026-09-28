-- WR-LSQ-9 (wave-5 LW4, lane lab-sql; l3-integration, L3-INTEGRATION-6a5f3df.md): the reads
-- L3's `Operations` (the /lab/v1/control route) and `Serving` (R2's ServingControl) make on
-- the control service's own login `infrx_lab_control` (0043, WR-I2L-4). They are plain
-- selects of 0007's registry in `PgControlStore` (provider_servings, provider_deployments,
-- endpoint_alias, listing_versions) plus `PgCatalogDirectory.active_rate_card`; 0043 gave
-- the login every table they read except `infrx.catalog_listings` (a Lab deployment's card
-- and an alias's listing versions). This file grants that one read and nothing else: the
-- listings are written only by 0032's publication/rollback CAS RPCs (SECURITY DEFINER).
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
-- ROLLBACK (this file alone; nothing references it): drop policy lab_control_reads_listings
--   on infrx.catalog_listings; revoke select on infrx.catalog_listings from infrx_lab_control.

grant select on infrx.catalog_listings to infrx_lab_control;
-- (0007 keeps row security on the registry: the login's read needs its own policy)
drop policy if exists lab_control_reads_listings on infrx.catalog_listings;
create policy lab_control_reads_listings on infrx.catalog_listings for select
  to infrx_lab_control using (true);
