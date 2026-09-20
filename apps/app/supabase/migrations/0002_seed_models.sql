-- Model catalog. Idempotent: re-running updates the rows in place.
-- Prices are $ per 1M tokens. Snippets use {{KEY}} and {{BASE_URL}}, which the
-- console replaces with the selected key and the model's base_url.

insert into public.models (
  id, name, provider, description, status, base_url, served_model,
  input_usd_per_m, output_usd_per_m, cache_usd_per_m,
  context_tokens, max_output_tokens,
  input_modalities, output_modalities, limits, snippets, sort)
values (
  'nemostation/marlin-2b',
  'Marlin 2B',
  'NemoStation',
  '2B video VLM (Qwen3.5-2B base, Apache-2.0) for dense scene+event captioning with second-precise timestamps and natural-language temporal grounding. Send one video (mp4/webm/mov, up to 2 minutes; sampled at 2 fps, max 240 frames) plus a text prompt.',
  'live',
  'https://marlin2b.callbill.ai/v1',
  'nemostation/marlin-2b',
  0.10, 0.30, null,
  32768, 2048,
  '{text,video}', '{text}',
  '{"max_video_seconds": 120, "max_video_mb": 64, "videos_per_request": 1}'::jsonb,
  jsonb_build_object(
    'curl', $snip$curl {{BASE_URL}}/chat/completions \
  -H "Authorization: Bearer {{KEY}}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nemostation/marlin-2b",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "video_url", "video_url": {"url": "https://download.samplelib.com/mp4/sample-10s.mp4"}},
          {"type": "text", "text": "Provide a spatial description of this clip followed by time-ranged events.\nFor each event, give the time range as <start - end> and a short description."}
        ]
      }
    ],
    "max_tokens": 512,
    "temperature": 0
  }'$snip$,
    'python', $snip$# pip install openai
from openai import OpenAI

client = OpenAI(api_key="{{KEY}}", base_url="{{BASE_URL}}")

response = client.chat.completions.create(
    model="nemostation/marlin-2b",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "video_url", "video_url": {"url": "https://download.samplelib.com/mp4/sample-10s.mp4"}},
                {"type": "text", "text": "Provide a spatial description of this clip followed by time-ranged events.\nFor each event, give the time range as <start - end> and a short description."},
            ],
        }
    ],
    max_tokens=512,
    temperature=0,
)

print(response.choices[0].message.content)$snip$,
    'javascript', $snip$// npm install openai
import OpenAI from "openai";

const client = new OpenAI({ apiKey: "{{KEY}}", baseURL: "{{BASE_URL}}" });

const response = await client.chat.completions.create({
  model: "nemostation/marlin-2b",
  messages: [
    {
      role: "user",
      content: [
        { type: "video_url", video_url: { url: "https://download.samplelib.com/mp4/sample-10s.mp4" } },
        { type: "text", text: "Provide a spatial description of this clip followed by time-ranged events.\nFor each event, give the time range as <start - end> and a short description." },
      ],
    },
  ],
  max_tokens: 512,
  temperature: 0,
});

console.log(response.choices[0].message.content);$snip$),
  10
)
on conflict (id) do update set
  name = excluded.name, provider = excluded.provider,
  description = excluded.description, status = excluded.status,
  base_url = excluded.base_url, served_model = excluded.served_model,
  input_usd_per_m = excluded.input_usd_per_m,
  output_usd_per_m = excluded.output_usd_per_m,
  cache_usd_per_m = excluded.cache_usd_per_m,
  context_tokens = excluded.context_tokens,
  max_output_tokens = excluded.max_output_tokens,
  input_modalities = excluded.input_modalities,
  output_modalities = excluded.output_modalities,
  limits = excluded.limits, snippets = excluded.snippets, sort = excluded.sort;

-- Coming soon. Prices are placeholders taken from public listings and are not
-- contractual; base_url points at the planned shared endpoint.
insert into public.models (
  id, name, provider, description, status, base_url, served_model,
  input_usd_per_m, output_usd_per_m, cache_usd_per_m,
  context_tokens, max_output_tokens,
  input_modalities, output_modalities, sort)
values
  ('deepseek-ai/DeepSeek-V4.1-Flash', 'DeepSeek V4.1 Flash', 'DeepSeek',
   '552B mixture-of-experts text model tuned for low-latency chat and code with a 1M-token context. Prices shown are placeholders from public listings and may change at launch.',
   'coming_soon', 'https://api.callbill.ai/v1', 'deepseek-ai/DeepSeek-V4.1-Flash',
   0.14, 0.60, null, 1000000, null, '{text}', '{text}', 20),

  ('Qwen/Qwen3.8-27B', 'Qwen3.8 27B', 'Qwen',
   'Dense 27B multimodal model accepting text, image and video input with a 262k-token context. Prices shown are placeholders from public listings and may change at launch.',
   'coming_soon', 'https://api.callbill.ai/v1', 'Qwen/Qwen3.8-27B',
   0.42, 3.00, null, 262144, null, '{text,image,video}', '{text}', 30),

  ('moonshotai/Kimi-K3', 'Kimi K3', 'Moonshot AI',
   '2.8T mixture-of-experts frontier model for agentic and long-context work, 1M-token context. Prices shown are placeholders from public listings and may change at launch.',
   'coming_soon', 'https://api.callbill.ai/v1', 'moonshotai/Kimi-K3',
   1.70, 8.50, null, 1000000, null, '{text}', '{text}', 40)
on conflict (id) do update set
  name = excluded.name, provider = excluded.provider,
  description = excluded.description, status = excluded.status,
  base_url = excluded.base_url, served_model = excluded.served_model,
  input_usd_per_m = excluded.input_usd_per_m,
  output_usd_per_m = excluded.output_usd_per_m,
  context_tokens = excluded.context_tokens,
  input_modalities = excluded.input_modalities,
  output_modalities = excluded.output_modalities,
  sort = excluded.sort;
