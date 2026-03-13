FROM golang:1.22-alpine AS builder

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o finit-orchestrator .

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/finit-orchestrator /usr/local/bin/
EXPOSE 8080
CMD ["finit-orchestrator"]
