import { prisma } from "@/lib/prisma";

interface LogActivityParams {
  userId?: string | null;
  userName?: string | null;
  userEmail?: string | null;
  action: string;
  entity: string;
  entityId?: string | null;
  details?: string | null;
  metadata?: Record<string, any> | null;
  ipAddress?: string | null;
}

/**
 * Log an activity event to the ActivityLog table.
 * Fire-and-forget — errors are caught and logged, never thrown.
 */
export async function logActivity(params: LogActivityParams): Promise<void> {
  try {
    await prisma.activityLog.create({
      data: {
        userId: params.userId ?? null,
        userName: params.userName ?? null,
        userEmail: params.userEmail ?? null,
        action: params.action,
        entity: params.entity,
        entityId: params.entityId ?? null,
        details: params.details ?? null,
        metadata: params.metadata ? JSON.stringify(params.metadata) : null,
        ipAddress: params.ipAddress ?? null,
      },
    });
  } catch (error) {
    console.error("Failed to log activity:", error);
  }
}
