-- Widen the artist language check to admit the non-Latin scripts the
-- classifier now detects: th (Thai), ru (Cyrillic), ar (Arabic). Latin-script
-- languages (es/fr/de/…) still collapse into 'en' — script detection alone
-- can't separate them; that would need statistical language ID.

alter table artists drop constraint artists_language_check;
alter table artists add constraint artists_language_check
    check (language in ('ja', 'en', 'ko', 'zh', 'th', 'ru', 'ar', 'unknown'));
