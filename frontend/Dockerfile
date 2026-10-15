# ============================================================================
# M-P 前端控制台 — 静态产物镜像（可选部署路径）
#
# SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-P / 第 4.2 章环境差异
# 覆盖 FR：FR-53~58（控制台交付）
# 对应 AC：AC-11（生产构建禁直传身份头）
#
# 两段式：node 构建 → nginx 托管静态产物。
# 注意（AC-11）：生产镜像**不得**设置 VITE_ALLOW_ACTOR_HEADER；
# 生产身份由 trusted-gateway 注入 HMAC 签名身份（FR-65）。
# ============================================================================

# ---- Stage 1：构建 ----
FROM node:24.16.0-alpine AS build

WORKDIR /app

# 先只拷贝清单，利用层缓存（依赖不变则不重装）
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

# 生产构建：client.ts 中 `isActorHeaderAllowed()` 在 import.meta.env.PROD 下恒为 false
RUN npm run build

# ---- Stage 2：托管 ----
FROM nginx:1.27-alpine

# SPA 回退 + API 反代（反代目标按部署环境覆盖）
RUN printf '%s\n' \
    'server {' \
    '  listen 80;' \
    '  server_name _;' \
    '  root /usr/share/nginx/html;' \
    '  index index.html;' \
    '' \
    '  # SPA：未命中的路径交给前端路由（History 模式）' \
    '  location / {' \
    '    try_files $uri $uri/ /index.html;' \
    '  }' \
    '' \
    '  # 控制面 API 反代（生产应由网关承担，此处仅作单容器演示）' \
    '  location /api/ {' \
    '    proxy_pass http://api:8000;' \
    '    proxy_set_header Host $host;' \
    '    proxy_set_header X-Real-IP $remote_addr;' \
    '    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' \
    '    proxy_set_header X-Forwarded-Proto $scheme;' \
    '  }' \
    '' \
    '  location /health {' \
    '    proxy_pass http://api:8000;' \
    '  }' \
    '}' > /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
