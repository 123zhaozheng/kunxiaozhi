// ============================================
// Feedback Types
// ============================================

// 评分值类型：up（好评）或 down（差评）
export type RatingValue = "up" | "down";

// 点踩原因枚举（仅 down 时有意义）
export type FeedbackReason =
  | "irrelevant"
  | "incomplete"
  | "incorrect"
  | "data_error";

export interface Feedback {
  id: string;
  user_id: string;
  username: string;
  session_id: string;
  run_id: string;
  rating: RatingValue;
  comment: string | null;
  reason: FeedbackReason | null;
  created_at: string;
}

export interface FeedbackCreate {
  session_id: string;
  run_id: string;
  rating: RatingValue;
  comment?: string;
  reason?: FeedbackReason;
}

export interface FeedbackStats {
  total_count: number;
  up_count: number;
  down_count: number;
  up_percentage: number;
}

export interface FeedbackListResponse {
  items: Feedback[];
  total: number;
  stats: FeedbackStats;
}
