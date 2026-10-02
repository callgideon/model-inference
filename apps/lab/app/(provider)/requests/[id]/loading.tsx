import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";

export default function Loading() {
  return (
    <div className="lab-stack">
      <PageHeader title="Request" breadcrumb={[{ href: "/requests", label: "Requests" }]} />
      <ServiceState state="loading" title="Loading request" explanation="Reading this request's record." />
    </div>
  );
}
