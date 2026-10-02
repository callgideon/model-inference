import { requireProviderWorkspace } from "@/lib/auth/guard";
import { configureJudge, judgeCalibrationPage, requestJudgeRun, setJudgeBudget } from "@/lib/services/judge/actions";
import { PageHeader } from "@/components/ui/page-header";
import { JudgeForm } from "./form";
import { JudgeRecords } from "./records";

export const metadata = { title: "Judge setup · infrx Lab" };

// WR-C3L-2: C3L's four actions. The run id is minted here, once per render: a double click posts
// the same id twice and the RPC answers one run. The provider is the guarded workspace, never a field.
// Register row 98: configure and budget carry a render-minted Idempotency-Key the form replaces after a
// definite answer; calibration names its configuration.
export default async function Judge() {
  const workspace = await requireProviderWorkspace();
  if (workspace.role === "viewer") return <p>Your role cannot configure or run the judge.</p>;
  const mint = () => crypto.randomUUID();
  const runId = mint();
  return (
    <>
      <PageHeader
        title="Judge setup"
        breadcrumb={[{ href: "/evaluations", label: "Evaluations" }]}
        purpose="A teacher model's judgment for evaluations scored by teacher judgment: a source of evidence, not ground truth."
      />
      <p>
        Judge runs are paid in PROVIDER_USD by a named payer of this workspace, never in CREDIT, and nothing is sent without
        the grantor&apos;s current external_judging grant.
      </p>
      {/* ponytail: unreadable until api-frontends-lab's judge port lists configs/runs/budgets (WR-UX08-2). */}
      <JudgeRecords records={null} />
      <JudgeForm
        action={configureJudge}
        title="Configure a judge"
        fields={[
          { name: "grantor_org_id", label: "Grantor organization id" },
          { name: "model_id", label: "Model id" },
          { name: "judge_model", label: "Judge model" },
          { name: "rubric_version", label: "Rubric version" },
          { name: "sample_size", label: "Sample size (1-200)" },
        ]}
        hidden={{ idempotency_key: mint() }}
        keyed="idempotency_key"
      />
      {workspace.role === "administrator" && (
        <JudgeForm
          action={setJudgeBudget}
          title="Set a budget"
          fields={[
            { name: "payer_ref", label: "Payer" },
            { name: "limit_usd", label: "Limit (PROVIDER_USD, 8 decimals)" },
          ]}
          hidden={{ idempotency_key: mint() }}
          keyed="idempotency_key"
        />
      )}
      <JudgeForm
        action={requestJudgeRun}
        title="Request a run"
        fields={[
          { name: "config_id", label: "Configuration id" },
          { name: "payer_ref", label: "Payer" },
        ]}
        hidden={{ run_id: runId }}
      />
      <JudgeForm
        action={judgeCalibrationPage}
        title="Check calibration"
        fields={[{ name: "config_id", label: "Configuration id" }]}
      />
    </>
  );
}
