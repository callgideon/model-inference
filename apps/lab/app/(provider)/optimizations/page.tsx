import { requireProviderWorkspace } from "@/lib/auth/guard";
import { isPreview, releasesPort } from "@/lib/services/rollouts/port";
import { PageHeader } from "@/components/ui/page-header";
import { PreviewNote } from "@/components/preview-note";
import { Variants } from "../releases/variants";

export const metadata = { title: "Optimizations · infrx Lab" };

// UX-10 (L-11): R3's registered variants as the Releases page's subordinate comparison view
// (releases/variants.tsx, tests/ux/releases UX10-P07). Each is a separate serving version scoped to the
// hardware and runtime it ran on; a performance figure is a measurement only from an experiment's results at a commit.
export default async function Optimizations() {
  const workspace = await requireProviderWorkspace();
  const variants = await releasesPort().variants(workspace);
  return (
    <>
      <PageHeader
        title="Optimizations"
        purpose="Optimized variants, each a separate serving version compared with its base on the same scope. A variant replaces nothing until a release does."
        breadcrumb={[{ href: "/releases", label: "Releases" }]}
      />
      {isPreview() && <PreviewNote records="variant" service="rollout" />}
      <Variants result={variants} />
    </>
  );
}
