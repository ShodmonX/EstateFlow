alter table announcement_media
    drop constraint if exists announcement_media_pkey;

alter table announcement_media
    add constraint pk_announcement_media
    primary key (announcement_id, media_id);
