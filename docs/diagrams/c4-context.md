# C4 Context -- Finit Platform

Верхний уровень: система, пользователь, внешние сервисы и границы.

```mermaid
graph TB
    User["Разработчик<br/><i>(Пользователь платформы)</i>"]

    subgraph Finit["Finit Platform"]
        direction TB
        Core["Finit Core<br/><i>Агентная платформа для<br/>автономной разработки ПО</i>"]
    end

    LLM["LLM-провайдеры<br/><i>vLLM, Ollama, OpenAI,<br/>Anthropic, Mock</i>"]
    VCS["Git-репозиторий<br/><i>Исходный код проекта</i>"]
    Docker["Docker Engine<br/><i>Контейнерная среда<br/>для рабочих окружений</i>"]

    User -->|"Задачи (WebUI/API)<br/>Решения (одобрение/отклонение)"| Core
    Core -->|"Результаты (diff + ревью)<br/>AG-UI события (SSE)"| User
    Core -->|"LLM-запросы<br/>(OpenAI-compatible API)"| LLM
    LLM -->|"Структурированный вывод<br/>(JSON, потоковая передача)"| Core
    Core -->|"Клонирование,<br/>чтение файлов"| VCS
    Core -->|"Сборка и запуск<br/>контейнеров"| Docker

    style Finit fill:#e8f4f8,stroke:#2196F3,stroke-width:2px
    style User fill:#fff3e0,stroke:#FF9800,stroke-width:2px
    style LLM fill:#fce4ec,stroke:#E91E63,stroke-width:2px
    style VCS fill:#e8f5e9,stroke:#4CAF50,stroke-width:2px
    style Docker fill:#f3e5f5,stroke:#9C27B0,stroke-width:2px
```

## Границы системы

| Элемент | Внутри системы | Вне системы |
|---|---|---|
| **Управление задачами** | Оркестратор управляет жизненным циклом | Пользователь одобряет/отклоняет |
| **LLM-вывод** | LLM Router маршрутизирует и отслеживает | Вычисления на внешних провайдерах |
| **Исполнение кода** | Агенты вызывают MCP-инструменты в окружениях | Docker Engine управляет контейнерами |
| **Хранение кода** | Docker volumes внутри платформы | Git-репозиторий -- внешний источник |
| **Наблюдаемость** | OTel, Prometheus, Grafana, MLFlow | Grafana/MLFlow -- отдельные процессы |
