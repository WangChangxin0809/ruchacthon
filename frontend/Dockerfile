FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

# The dev server, not a production build: this is a hackathon dashboard
# meant to be poked at and iterated on, not deployed. --host so it's
# reachable from outside the container, --strictPort so a silent port
# fallback never leaves docker-compose's published port pointing at nothing.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173", "--strictPort"]
