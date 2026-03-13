package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
)

// Task represents a unit of work flowing through the pipeline.
type Task struct {
	ID        string          `json:"id"`
	Status    string          `json:"status"`
	Input     string          `json:"input"`
	Domains   []string        `json:"domains,omitempty"`
	Spec      json.RawMessage `json:"spec,omitempty"`
	Code      json.RawMessage `json:"code,omitempty"`
	Review    json.RawMessage `json:"review,omitempty"`
	Error     string          `json:"error,omitempty"`
	CreatedAt time.Time       `json:"created_at"`
	UpdatedAt time.Time       `json:"updated_at"`
}

// TaskStore is an in-memory task store backed by Redis pub/sub for agent coordination.
type TaskStore struct {
	mu    sync.RWMutex
	tasks map[string]*Task
	rdb   *redis.Client
}

func NewTaskStore(rdb *redis.Client) *TaskStore {
	return &TaskStore{
		tasks: make(map[string]*Task),
		rdb:   rdb,
	}
}

func (s *TaskStore) Create(input string) *Task {
	s.mu.Lock()
	defer s.mu.Unlock()

	id := fmt.Sprintf("task-%d", time.Now().UnixNano())
	t := &Task{
		ID:        id,
		Status:    "created",
		Input:     input,
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	s.tasks[id] = t
	return t
}

func (s *TaskStore) Get(id string) *Task {
	s.mu.RLock()
	defer s.mu.RUnlock()
	t, ok := s.tasks[id]
	if !ok {
		return nil
	}
	cp := *t
	return &cp
}

func (s *TaskStore) List() []*Task {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*Task, 0, len(s.tasks))
	for _, t := range s.tasks {
		cp := *t
		out = append(out, &cp)
	}
	return out
}

func (s *TaskStore) Update(id string, fn func(*Task)) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tasks[id]
	if !ok {
		return false
	}
	fn(t)
	t.UpdatedAt = time.Now()
	return true
}

// publishToStream pushes a task event into a Redis stream for agents to consume.
func (s *TaskStore) publishToStream(ctx context.Context, stream string, task *Task) error {
	data, err := json.Marshal(task)
	if err != nil {
		return err
	}
	return s.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		Values: map[string]interface{}{"data": string(data)},
	}).Err()
}

// Server holds HTTP handlers and dependencies.
type Server struct {
	store *TaskStore
	rdb   *redis.Client
}

func (s *Server) handleCreateTask(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct {
		Input string `json:"input"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Input == "" {
		http.Error(w, "invalid request: input required", http.StatusBadRequest)
		return
	}

	task := s.store.Create(req.Input)

	// Publish to the routing stream for the router agent.
	ctx := r.Context()
	if err := s.store.publishToStream(ctx, "tasks:pending_routing", task); err != nil {
		log.Printf("ERROR publishing to stream: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	log.Printf("Task created: %s", task.ID)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(task)
}

func (s *Server) handleGetTask(w http.ResponseWriter, r *http.Request) {
	id := r.URL.Query().Get("id")
	if id == "" {
		http.Error(w, "id required", http.StatusBadRequest)
		return
	}
	task := s.store.Get(id)
	if task == nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(task)
}

func (s *Server) handleListTasks(w http.ResponseWriter, r *http.Request) {
	tasks := s.store.List()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(tasks)
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := s.rdb.Ping(ctx).Err(); err != nil {
		http.Error(w, "redis unhealthy", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// listenForAgentResults subscribes to result streams and updates task state.
func (s *Server) listenForAgentResults(ctx context.Context) {
	streams := []string{
		"tasks:routed",
		"tasks:specced",
		"tasks:developed",
		"tasks:reviewed",
	}

	// Create consumer groups (ignore errors if already exist).
	for _, stream := range streams {
		s.rdb.XGroupCreateMkStream(ctx, stream, "orchestrator", "0").Err()
	}

	// Build XREADGROUP args: stream1 stream2 ... > > ...
	readStreams := make([]string, 0, len(streams)*2)
	for _, st := range streams {
		readStreams = append(readStreams, st)
	}
	for range streams {
		readStreams = append(readStreams, ">")
	}

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		results, err := s.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
			Group:    "orchestrator",
			Consumer: "orch-1",
			Streams:  readStreams,
			Count:    10,
			Block:    time.Second * 2,
		}).Result()

		if err != nil {
			if err != redis.Nil {
				// Don't spam logs on context cancellation.
				if ctx.Err() == nil {
					log.Printf("XREADGROUP error: %v", err)
				}
			}
			continue
		}

		for _, stream := range results {
			for _, msg := range stream.Messages {
				s.processAgentMessage(ctx, stream.Stream, msg)
			}
		}
	}
}

func (s *Server) processAgentMessage(ctx context.Context, stream string, msg redis.XMessage) {
	data, ok := msg.Values["data"].(string)
	if !ok {
		log.Printf("invalid message in %s: %s", stream, msg.ID)
		return
	}

	var incoming Task
	if err := json.Unmarshal([]byte(data), &incoming); err != nil {
		log.Printf("unmarshal error in %s: %v", stream, err)
		return
	}

	log.Printf("Received on %s: task=%s", stream, incoming.ID)

	switch stream {
	case "tasks:routed":
		s.store.Update(incoming.ID, func(t *Task) {
			t.Status = "routed"
			t.Domains = incoming.Domains
		})
		// Forward to spec generation.
		s.store.publishToStream(ctx, "tasks:pending_spec", &incoming)

	case "tasks:specced":
		s.store.Update(incoming.ID, func(t *Task) {
			t.Status = "specced"
			t.Spec = incoming.Spec
		})
		// Forward to development.
		s.store.publishToStream(ctx, "tasks:pending_dev", &incoming)

	case "tasks:developed":
		s.store.Update(incoming.ID, func(t *Task) {
			t.Status = "developed"
			t.Code = incoming.Code
		})
		// Forward to review.
		s.store.publishToStream(ctx, "tasks:pending_review", &incoming)

	case "tasks:reviewed":
		s.store.Update(incoming.ID, func(t *Task) {
			t.Status = "completed"
			t.Review = incoming.Review
		})
		log.Printf("Task %s COMPLETED", incoming.ID)
	}

	// ACK the message.
	s.rdb.XAck(ctx, stream, "orchestrator", msg.ID)
}

func main() {
	redisAddr := os.Getenv("REDIS_URL")
	if redisAddr == "" {
		redisAddr = "redis:6379"
	}

	rdb := redis.NewClient(&redis.Options{
		Addr: redisAddr,
	})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Wait for Redis to be ready.
	for i := 0; i < 30; i++ {
		if err := rdb.Ping(ctx).Err(); err == nil {
			break
		}
		log.Printf("Waiting for Redis at %s...", redisAddr)
		time.Sleep(time.Second)
	}

	store := NewTaskStore(rdb)
	srv := &Server{store: store, rdb: rdb}

	// Start listening for agent results in background.
	go srv.listenForAgentResults(ctx)

	mux := http.NewServeMux()
	mux.HandleFunc("/api/tasks", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodPost:
			srv.handleCreateTask(w, r)
		case http.MethodGet:
			srv.handleListTasks(w, r)
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})
	mux.HandleFunc("/api/task", srv.handleGetTask)
	mux.HandleFunc("/health", srv.handleHealth)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	httpSrv := &http.Server{
		Addr:    ":" + port,
		Handler: mux,
	}

	go func() {
		log.Printf("Orchestrator listening on :%s", port)
		if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("HTTP server error: %v", err)
		}
	}()

	// Graceful shutdown.
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh
	log.Println("Shutting down...")
	cancel()
	httpSrv.Shutdown(context.Background())
}
