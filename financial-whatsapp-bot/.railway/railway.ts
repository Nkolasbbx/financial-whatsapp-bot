import { defineRailway, github, preserve, project, redis, service, volume } from "railway/iac";

export default defineRailway(() => {
  const financialWhatsappBotRepo = github("Nkolasbbx/financial-whatsapp-bot", { checkSuites: false, rootDirectory: "/financial-whatsapp-bot" });

  const Redis = redis("Redis", { region: "ams" });
  Redis.deploy = { startCommand: "/bin/sh -c \"rm -rf $RAILWAY_VOLUME_MOUNT_PATH/lost+found/ && exec docker-entrypoint.sh redis-server --requirepass $REDIS_PASSWORD --save 60 1 --dir $RAILWAY_VOLUME_MOUNT_PATH\"" };
  const redisVolumeLXG = volume("redis-volume-LX-G", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "ams", sizeMB: 500 });
  const financialWhatsappBot = service("financial-whatsapp-bot", {
    source: financialWhatsappBotRepo,
    replicas: { "ams": 1 },
    build: { builder: "RAILPACK" },
    deploy: {
      startCommand: "uvicorn main:app --host 0.0.0.0 --port $PORT",
      healthcheckPath: "/",
      healthcheckTimeout: 30,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    env: { CRON_SECRET: preserve(), DB_DSN: preserve(), DEBUG: preserve(), EMBEDDING_MODEL_NAME: preserve(), HF_TOKEN: preserve(), IA_API_KEY: preserve(), META_APP_SECRET: preserve(), META_GRAPH_API_VERSION: preserve(), META_PHONE_NUMBER_ID: preserve(), META_WABA_ID: preserve(), META_WEBHOOK_VERIFY_TOKEN: preserve(), META_WHATSAPP_TOKEN: preserve(), OLLAMA_MODEL: preserve(), OLLAMA_URL: preserve(), REDIS_URL: preserve(), REMINDERS_ENABLED: preserve(), REMINDER_BATCH_SIZE: preserve(), REMINDER_DAYS: preserve(), REMINDER_FINAL_TEMPLATE_NAME: preserve(), REMINDER_RECIPIENT_LABEL: preserve(), REMINDER_TEMPLATE_LANGUAGE: preserve(), REMINDER_TEMPLATE_NAME: preserve(), REMINDER_TIMEZONE: preserve(), RES_KEY: preserve(), RES_MODEL: preserve(), RES_URL: preserve(), SUPABASE_KEY: preserve(), SUPABASE_SERVICE_ROLE_KEY: preserve(), SUPABASE_URL: preserve(), TWILIO_ACCOUNT_SID: preserve(), TWILIO_AUTH_TOKEN: preserve(), TWILIO_WHATSAPP_NUMBER: preserve(), WHATSAPP_PROVIDER: preserve() },
  });

  // El worker no tiene credenciales propias: dependencies.init_dependencies()
  // (llamado desde worker.py on_startup) necesita las mismas variables que
  // usa el server (Supabase, DB_DSN, Meta WhatsApp, Redis, etc.), así que se
  // referencian directamente desde financial-whatsapp-bot en vez de duplicar
  // secretos. Si se agrega una variable nueva al server que el worker
  // también necesite, hay que agregarla acá también.
  const incredibleAdventure = service("incredible-adventure", {
    source: financialWhatsappBotRepo,
    replicas: { "ams": 1 },
    build: { builder: "RAILPACK" },
    deploy: {
      startCommand: "arq worker.WorkerSettings",
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    env: {
      CRON_SECRET: financialWhatsappBot.env.CRON_SECRET,
      DB_DSN: financialWhatsappBot.env.DB_DSN,
      DEBUG: financialWhatsappBot.env.DEBUG,
      EMBEDDING_MODEL_NAME: financialWhatsappBot.env.EMBEDDING_MODEL_NAME,
      HF_TOKEN: financialWhatsappBot.env.HF_TOKEN,
      IA_API_KEY: financialWhatsappBot.env.IA_API_KEY,
      META_APP_SECRET: financialWhatsappBot.env.META_APP_SECRET,
      META_GRAPH_API_VERSION: financialWhatsappBot.env.META_GRAPH_API_VERSION,
      META_PHONE_NUMBER_ID: financialWhatsappBot.env.META_PHONE_NUMBER_ID,
      META_WABA_ID: financialWhatsappBot.env.META_WABA_ID,
      META_WEBHOOK_VERIFY_TOKEN: financialWhatsappBot.env.META_WEBHOOK_VERIFY_TOKEN,
      META_WHATSAPP_TOKEN: financialWhatsappBot.env.META_WHATSAPP_TOKEN,
      OLLAMA_MODEL: financialWhatsappBot.env.OLLAMA_MODEL,
      OLLAMA_URL: financialWhatsappBot.env.OLLAMA_URL,
      REDIS_URL: financialWhatsappBot.env.REDIS_URL,
      REMINDERS_ENABLED: financialWhatsappBot.env.REMINDERS_ENABLED,
      REMINDER_BATCH_SIZE: financialWhatsappBot.env.REMINDER_BATCH_SIZE,
      REMINDER_DAYS: financialWhatsappBot.env.REMINDER_DAYS,
      REMINDER_FINAL_TEMPLATE_NAME: financialWhatsappBot.env.REMINDER_FINAL_TEMPLATE_NAME,
      REMINDER_RECIPIENT_LABEL: financialWhatsappBot.env.REMINDER_RECIPIENT_LABEL,
      REMINDER_TEMPLATE_LANGUAGE: financialWhatsappBot.env.REMINDER_TEMPLATE_LANGUAGE,
      REMINDER_TEMPLATE_NAME: financialWhatsappBot.env.REMINDER_TEMPLATE_NAME,
      REMINDER_TIMEZONE: financialWhatsappBot.env.REMINDER_TIMEZONE,
      RES_KEY: financialWhatsappBot.env.RES_KEY,
      RES_MODEL: financialWhatsappBot.env.RES_MODEL,
      RES_URL: financialWhatsappBot.env.RES_URL,
      SUPABASE_KEY: financialWhatsappBot.env.SUPABASE_KEY,
      SUPABASE_SERVICE_ROLE_KEY: financialWhatsappBot.env.SUPABASE_SERVICE_ROLE_KEY,
      SUPABASE_URL: financialWhatsappBot.env.SUPABASE_URL,
      TWILIO_ACCOUNT_SID: financialWhatsappBot.env.TWILIO_ACCOUNT_SID,
      TWILIO_AUTH_TOKEN: financialWhatsappBot.env.TWILIO_AUTH_TOKEN,
      TWILIO_WHATSAPP_NUMBER: financialWhatsappBot.env.TWILIO_WHATSAPP_NUMBER,
      WHATSAPP_PROVIDER: financialWhatsappBot.env.WHATSAPP_PROVIDER,
    },
  });

  return project("stellar-recreation", {
    resources: [incredibleAdventure, Redis, financialWhatsappBot, redisVolumeLXG],
  });
});
