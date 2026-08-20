export interface I18nText {
  en: string;
  zh: string;
  ja: string;
  ko: string;
  ru: string;
}

export type NotificationType = "info" | "success" | "warning" | "maintenance";

export interface Notification {
  id: string;
  title_i18n: I18nText;
  content_i18n: I18nText;
  type: NotificationType;
  start_time: string | null;
  end_time: string | null;
  is_active: boolean;
  popup: boolean;
  should_popup?: boolean;
  created_at: string;
  updated_at: string;
  created_by: string;
}

export interface NotificationCreate {
  title_i18n: I18nText;
  content_i18n: I18nText;
  type?: NotificationType;
  start_time: string | null;
  end_time: string | null;
  is_active: boolean;
  popup?: boolean;
}

export interface NotificationUpdate {
  title_i18n?: I18nText;
  content_i18n?: I18nText;
  type?: NotificationType;
  start_time?: string | null;
  end_time?: string | null;
  is_active?: boolean;
  popup?: boolean;
}

export interface NotificationListResponse {
  items: Notification[];
  total: number;
}
