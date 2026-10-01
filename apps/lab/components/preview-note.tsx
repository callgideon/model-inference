/** The preview stand-in's label (LAB-09: one note for the six inline copies); shown only while a family's preview is on. */
export function PreviewNote({ records, service }: { records: string; service: string }) {
  return <p role="note">{`Preview: ${records} records come from an in-memory stand-in, not the ${service} service.`}</p>;
}
