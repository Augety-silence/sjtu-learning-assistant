import {
  archiveStatusLabel,
  archiveStatusTone,
} from "@/components/archive/archiveUtils";

export function ArchiveStatusChip({ status }: { status: string }) {
  return (
    <span
      className={"archive-status archive-status--" + archiveStatusTone(status)}
    >
      <span aria-hidden="true" />
      {archiveStatusLabel(status)}
    </span>
  );
}
