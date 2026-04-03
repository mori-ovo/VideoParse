export type Platform = 'bilibili' | 'douyin' | 'twitter' | 'youtube' | 'reddit'
export type DeliveryMode = 'auto' | 'direct' | 'download'

export type TaskStatus =
  | 'pending'
  | 'parsing'
  | 'downloading'
  | 'merging'
  | 'uploading'
  | 'success'
  | 'failed'

export type ResultType = 'direct' | 'download' | 'split_streams'

export interface TaskResult {
  result_type: ResultType
  file_id: string | null
  file_name: string | null
  content_type: string | null
  play_url: string | null
  download_url: string | null
  direct_url: string | null
  redirect_url: string | null
  proxy_url: string | null
  video_url: string | null
  video_redirect_url: string | null
  video_proxy_url: string | null
  audio_url: string | null
  audio_redirect_url: string | null
  audio_proxy_url: string | null
  placeholder: boolean
  created_at: string
  file_size: number | null
  expires_note: string | null
}

export interface TaskRecord {
  task_id: string
  source_url: string
  platform: Platform
  delivery_mode: DeliveryMode
  status: TaskStatus
  progress: number
  title: string
  message: string
  requires_merge: boolean
  direct_playable: boolean
  created_at: string
  updated_at: string
  result: TaskResult | null
  error_message: string | null
  uploader: string | null
  duration: number | null
  thumbnail: string | null
  extractor: string | null
}

export interface ParseRequest {
  url: string
  delivery_mode?: DeliveryMode
}

export interface ParseAcceptedResponse {
  task: TaskRecord
  note: string
}

export interface HealthResponse {
  status: string
  app_name: string
  cleanup_interval_hours: number
  cleanup_retention_hours: number
  api_public_origin: string
  yt_dlp_available: boolean
  ffmpeg_available: boolean
  default_delivery_mode: DeliveryMode
  supported_platforms: string[]
}
