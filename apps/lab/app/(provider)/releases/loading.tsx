import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";
import { PURPOSE } from "./page";

export default function Loading() {
  return (
    <>
      <PageHeader title="Releases" purpose={PURPOSE} />
      <ServiceState state="loading" title="Loading releases" explanation="Reading release records and publication requests." />
    </>
  );
}
