CREATE TABLE IF NOT EXISTS public.tahoma_events (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    event_type VARCHAR(255) NOT NULL,
    context_id VARCHAR(255),
    page_id VARCHAR(255),
    details JSONB
);

-- Optional: Index for faster lookups by time
CREATE INDEX idx_tahoma_events_timestamp ON public.tahoma_events(timestamp);