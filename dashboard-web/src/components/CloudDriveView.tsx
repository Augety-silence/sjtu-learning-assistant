import { FolderSync, HardDrive } from "lucide-react";
import { useState } from "react";
import { BackupView } from "@/components/BackupView";
import { ContentReveal } from "@/components/ContentReveal";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { DriveBrowser } from "./cloud-drive/DriveBrowser";
import { ConflictDialog } from "./cloud-drive/DriveDialogs";
import {
  driveTransfers,
  useDriveTransfers,
} from "./cloud-drive/driveTransfers";
import { TransferTray } from "./cloud-drive/TransferTray";
import "./cloud-drive.css";

type DriveTab = "files" | "sync";

/**
 * 交大云盘页面：主体是可浏览、上传、下载、整理的文件管理器；
 * 原“云盘备份”工作流降级为「本地同步」子页签。
 */
export function CloudDriveView() {
  const [tab, setTab] = useState<DriveTab>("files");
  const { conflict } = useDriveTransfers();
  return (
    <ContentReveal className="section-stack drive-page" afterLoad>
      <Tabs value={tab} onValueChange={(value) => setTab(value as DriveTab)}>
        <div className="drive-page-head">
          <TabsList indicator indicatorRadius={8} aria-label="交大云盘视图">
            <TabsTrigger value="files">
              <HardDrive aria-hidden="true" className="drive-tab-icon" />
              我的文件
            </TabsTrigger>
            <TabsTrigger value="sync">
              <FolderSync aria-hidden="true" className="drive-tab-icon" />
              本地同步
            </TabsTrigger>
          </TabsList>
        </div>
        {/* 文件浏览保持挂载：切到「本地同步」再回来时保留所在文件夹、选择与滚动位置。 */}
        <TabsContent value="files" forceMount className="drive-tab-panel">
          <DriveBrowser active={tab === "files"} />
        </TabsContent>
        <TabsContent value="sync" className="drive-tab-panel">
          <BackupView />
        </TabsContent>
      </Tabs>
      <TransferTray />
      {conflict && (
        <ConflictDialog
          key={conflict.id}
          name={conflict.name}
          remaining={conflict.remaining}
          onChoose={(choice, applyAll) =>
            driveTransfers.resolveConflict(choice, applyAll)
          }
        />
      )}
    </ContentReveal>
  );
}
