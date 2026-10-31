-- UMB timetable catalog, parser matchers, and integration link identity.

CREATE TABLE timetable_directory_pages (
    page_url TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE timetable_link_matchers (
    id TEXT PRIMARY KEY,
    url_glob TEXT NOT NULL UNIQUE,
    parser_key TEXT NOT NULL,
    degree TEXT NOT NULL,
    semester INTEGER NOT NULL CHECK (semester BETWEEN 1 AND 12),
    study_mode TEXT NOT NULL CHECK (study_mode IN ('stacjonarne','niestacjonarne')),
    availability TEXT NOT NULL DEFAULT 'available' CHECK (availability IN ('available','missing')),
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO timetable_directory_pages(page_url,label,position,created_at,updated_at) VALUES
('https://www.umb.edu.pl/pl/s,160/Harmonogramy_zajec','Lekarski',0,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wl/kierunek-lekarsko-dentystyczny/informacje_dla_studentow/harmonogramy_zajec','Lekarsko-Dentystyczny',1,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/s,182/Harmonogramy_zajec','Techniki Dentystyczne',2,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/s,21977/harmonogramy_i_plany','Higiena Stomatologiczna',3,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/s,25845/Harmonogramy__zajec','Biostatystyka Kliniczna I',4,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/s,29333/Harmonogramy_zajec','Biostatystyka Kliniczna II',5,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wl/bioinformatyka_medyczna/informacje_dla_studentow/harmonogramy_zajec','Bioinformatyka Medyczna',6,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wf/analityka-medyczna/informacje_dla_studentow/harmonogramy','Analityka Medyczna',7,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wf/farmacja/informacje_dla_studentow/harmonogramy','Farmacja',8,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wf/kosmetologia/informacje_dla_studentow/harmonogramy','Kosmetologia',9,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wnoz/dietetyka/informacje/rozklady_zajec','Dietetyka',10,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/s,8452/Rozklady_zajec','Elektroradiologia',11,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wnoz/fizjoterapia/informacje/rozklady','Fizjoterapia',12,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wnoz/logopedia/rozklady','Logopedia z Fonoaudiologią',13,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wnoz/pielegniarstwo/rozklady','Pielęgniarstwo',14,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wnoz/poloznictwo/informacje/rozklady','Położnictwo',15,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/s,32680/Harmonogramy_zajec','Psychologia',16,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wnoz/ratownictwo/rozklady','Ratownictwo Medyczne',17,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/s,23654/Harmonogramy','Zdrowie Publiczne i Epidemiologia',18,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('https://www.umb.edu.pl/wnoz/zdrowie_publiczne/informacje/rozklady','Zdrowie Publiczne II',19,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00');

INSERT INTO timetable_link_matchers(id,url_glob,parser_key,degree,semester,study_mode,position,created_at,updated_at) VALUES
('m1-01','/photo/pliki/Dziekanat-WL/magda/*/i_rok_lekarski_stacjonarny_*.xlsx','M1','Lekarski',1,'stacjonarne',0,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m1-02','/photo/pliki/Dziekanat-WL/magda/*/ii_rok_lekarski_stacjonarny_*.xlsx','M1','Lekarski',2,'stacjonarne',1,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m1-03','/photo/pliki/Dziekanat-WL/magda/*/i_rok_lekarski_niestacjonarny_*.xlsx','M1','Lekarski',1,'niestacjonarne',2,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m1-04','/photo/pliki/Dziekanat-WL/magda/*/ii_rok_lekarski_niestacjonarny_*.xlsx','M1','Lekarski',2,'niestacjonarne',3,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-01','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/fizjoterapia/*/1_fizjoterapia_*.xlsx','G0','Fizjoterapia',1,'stacjonarne',4,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-02','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/poloznictwo/*/1_pz_*.xlsx','G0','Położnictwo I stopnia',1,'stacjonarne',5,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-04','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/poloznictwo/*/mgr_1_pz_*.xlsx','G0','Położnictwo II stopnia',1,'stacjonarne',6,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-06','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/poloznictwo/*/2_pz_*.xlsx','G0','Położnictwo I stopnia',2,'stacjonarne',7,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-08','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/poloznictwo/*/mgr_2_pz_*.xlsx','G0','Położnictwo II stopnia',2,'stacjonarne',8,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-10','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/pielegniarstwo/*/1_pl_*.xlsx','G0','Pielęgniarstwo I stopnia',1,'stacjonarne',9,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-12','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/pielegniarstwo/*/mgr_1_pl_*.xlsx','G0','Pielęgniarstwo II stopnia',1,'stacjonarne',10,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-14','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/pielegniarstwo/*/2_pl_*.xlsx','G0','Pielęgniarstwo I stopnia',2,'stacjonarne',11,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-16','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/pielegniarstwo/*/mgr_2_pl_*.xlsx','G0','Pielęgniarstwo II stopnia',2,'stacjonarne',12,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-18','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/ratownictwo_medyczne/*/1_ratownictwo_medyczne_*.xlsx','G0','Ratownictwo Medyczne I stopnia',1,'stacjonarne',13,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-20','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/ratownictwo_medyczne/*/mgr_1_ratownictwo_medyczne_*.xlsx','G0','Ratownictwo Medyczne II stopnia',1,'stacjonarne',14,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-01','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarsko_dentystyczny/*/i_rok_lekarsko-dentystyczny_*.xlsx','G1','Lekarsko-Dentystyczny',1,'stacjonarne',15,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-22','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/elektroradiologia/2_rok_elektroradiologia_ii_st.*.xlsx','G0','Elektroradiologia',2,'stacjonarne',16,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-02','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/techniki_dentystyczne/*/i_rok_techniki_dentystyczne_*.xlsx','G1','Techniki Dentystyczne',1,'stacjonarne',17,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-03','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/higiena_stomatologiczna/*/i_rok_higiena_stomatologiczna_*.xlsx','G1','Higiena Stomatologiczna',1,'stacjonarne',18,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-04','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/biostatystyka_kliniczna/*/i_rok_i_st._biostatystyka_*.xlsx','G1','Biostatystyka Kliniczna I stopnia',1,'stacjonarne',19,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-05','/photo/pliki/administracja/sekcja_ds_planowania/1_rok_analityka_medyczna_*.xlsx','G1','Analityka Medyczna',1,'stacjonarne',20,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-06','/photo/pliki/administracja/sekcja_ds_planowania/1_rok_farmacja_*.xlsx','G1','Farmacja',1,'stacjonarne',21,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-07','/photo/pliki/administracja/sekcja_ds_planowania/1_rok_kosmetologia_i_stopnia_*.xlsx','G1','Kosmetologia',1,'stacjonarne',22,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-08','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/dietetyka_*/1_rok_i_st._dietetyka_*.xlsx','G1','Dietetyka I stopnia',1,'stacjonarne',23,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-09','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/dietetyka_*/1_rok_dietetyka_ii_stopien_*.xlsx','G1','Dietetyka II stopnia',1,'stacjonarne',24,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-10','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/logopedia_*/1_rok_logopedia_z_fonoaudiologia_i_st.*.xlsx','G1','Logopedia z Fonoaudiologią I stopnia',1,'stacjonarne',25,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-11','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/logopedia_*/1_rok_logopedia_z_fonoaudiologia_ii_st.*.xlsx','G1','Logopedia z Fonoaudiologią II stopnia',1,'stacjonarne',26,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-12','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/zdrowie_publiczne_/1_rok_zdrowie_publiczne_ii_st.*.xlsx','G1','Zdrowie Publiczne II',1,'stacjonarne',27,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-13','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/zdrowie_publiczne_i_epidemiologia_i_stopien/zdrowie_publiczne_*/1_rok_zdrowie_publiczne_i_epidemiologia_*.xlsx','G1','Zdrowie Publiczne i Epidemiologia',1,'stacjonarne',28,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-14','/photo/pliki/administracja/sekcja_ds_planowania/*/1_rok_psychologia_*.xlsx','G1','Psychologia',1,'stacjonarne',29,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-21','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/pielegniarstwo/*/nst_1_pl_*.xlsx','G1','Pielęgniarstwo II stopnia',1,'niestacjonarne',30,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-25','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/ratownictwo_medyczne/*/2_ratownictwo_medyczne_*.xlsx','G1','Ratownictwo Medyczne I stopnia',2,'stacjonarne',31,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-29','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/elektroradiologia/1_rok_elektroradiologia_i_st.*.xlsx','G1','Elektroradiologia',1,'stacjonarne',32,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-fiz-2','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/fizjoterapia/*/2_fizjoterapia_*.xlsx','G0','Fizjoterapia',2,'stacjonarne',33,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-fiz-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/fizjoterapia/*/3_fizjoterapia_*.xlsx','G0','Fizjoterapia',3,'stacjonarne',34,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-fiz-4','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/fizjoterapia/*/4_fizjoterapia_*.xlsx','G0','Fizjoterapia',4,'stacjonarne',35,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-fiz-5','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/fizjoterapia/*/5_fizjoterapia_*.xlsx','G0','Fizjoterapia',5,'stacjonarne',36,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-ld-2','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarsko_dentystyczny/*/ii_rok_lekarsko-dentystyczny_*.xlsx','G1','Lekarsko-Dentystyczny',2,'stacjonarne',37,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-ld-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarsko_dentystyczny/*/iii_rok_lekarsko-dentystyczny_*.xlsx','G1','Lekarsko-Dentystyczny',3,'stacjonarne',38,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-ld-4','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarsko_dentystyczny/*/iv_rok_lekarsko-dentystyczny_*.xlsx','G1','Lekarsko-Dentystyczny',4,'stacjonarne',39,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-ld-5','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarsko_dentystyczny/*/v_rok_lekarsko-dentystyczny_*.xlsx','G1','Lekarsko-Dentystyczny',5,'stacjonarne',40,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-td-2','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/techniki_dentystyczne/*/ii_rok_techniki_dentystyczne_*.xlsx','G1','Techniki Dentystyczne',2,'stacjonarne',41,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-td-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/techniki_dentystyczne/*/iii_rok_techniki_dentystyczne_*.xlsx','G1','Techniki Dentystyczne',3,'stacjonarne',42,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-hs-2','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/higiena_stomatologiczna/*/ii_rok_higiena_stomatologiczna_*.xlsx','G1','Higiena Stomatologiczna',2,'stacjonarne',43,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-hs-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/higiena_stomatologiczna/*/iii_rok_higiena_stomatologiczna_*.xlsx','G1','Higiena Stomatologiczna',3,'stacjonarne',44,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-diet-i-2','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/dietetyka_*/2_rok_i_st._dietetyka_*.xlsx','G1','Dietetyka I stopnia',2,'stacjonarne',45,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-diet-i-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/dietetyka_*/3_rok_i_st._dietetyka_*.xlsx','G1','Dietetyka I stopnia',3,'stacjonarne',46,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-diet-ii-2','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/dietetyka_*/2_rok_dietetyka_ii_stopien_*.xlsx','G1','Dietetyka II stopnia',2,'stacjonarne',47,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-pl-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/pielegniarstwo/*/3_pl_*.xlsx','G0','Pielęgniarstwo I stopnia',3,'stacjonarne',48,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-pz-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/poloznictwo/*/3_pz_*.xlsx','G0','Położnictwo I stopnia',3,'stacjonarne',49,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g0-rat-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/ratownictwo_medyczne/*/3_ratownictwo_medyczne_*.xlsx','G0','Ratownictwo Medyczne I stopnia',3,'stacjonarne',50,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m2-lek-st-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarski_eb/st/3_pl_st_*.xlsx','M2','Lekarski',3,'stacjonarne',51,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m2-lek-st-4','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarski_eb/st/4_pl_st_*.xlsx','M2','Lekarski',4,'stacjonarne',52,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m2-lek-st-5','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarski_eb/st/5_pl_st_*.xlsx','M2','Lekarski',5,'stacjonarne',53,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m2-lek-st-6','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarski_eb/st/6_pl_st_*.xlsx','M2','Lekarski',6,'stacjonarne',54,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m2-lek-nst-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarski_eb/nst/3_pl_nst_*.xlsx','M2','Lekarski',3,'niestacjonarne',55,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m2-lek-nst-4','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarski_eb/nst/4_pl_nst_*.xlsx','M2','Lekarski',4,'niestacjonarne',56,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m2-lek-nst-5','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarski_eb/nst/5_pl_nst_*.xlsx','M2','Lekarski',5,'niestacjonarne',57,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('m2-lek-nst-6','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/lekarski_eb/nst/6_pl_nst_*.xlsx','M2','Lekarski',6,'niestacjonarne',58,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-biostat-i-2','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/biostatystyka_kliniczna/*/ii_rok_i_st._biostatystyka_*.xlsx','G1','Biostatystyka Kliniczna I stopnia',2,'stacjonarne',59,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-biostat-i-3','/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/biostatystyka_kliniczna/*/iii_rok_biostatystyka_*.xlsx','G1','Biostatystyka Kliniczna I stopnia',3,'stacjonarne',60,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-kos-2','/photo/pliki/administracja/sekcja_ds_planowania/2_rok_kosmetologia_i_stopnia_*.xlsx','G1','Kosmetologia',2,'stacjonarne',61,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00'),
('g1-kos-3','/photo/pliki/administracja/sekcja_ds_planowania/3_rok_kosmetologia_i_stopnia_*.xlsx','G1','Kosmetologia',3,'stacjonarne',62,'2026-10-28T00:00:00+00:00','2026-10-28T00:00:00+00:00');

CREATE INDEX timetable_link_matchers_position_idx
    ON timetable_link_matchers(position,id);

CREATE UNIQUE INDEX timetable_link_matchers_choice_idx
    ON timetable_link_matchers(degree,semester,study_mode);

DROP TABLE timetable_links;

CREATE TABLE timetable_links (
    url TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    programme TEXT NOT NULL,
    page_url TEXT NOT NULL,
    parser_key TEXT NOT NULL,
    link_key TEXT NOT NULL,
    last_modified TEXT,
    discovered_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    listed INTEGER NOT NULL DEFAULT 1 CHECK (listed IN (0,1)),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX timetable_links_active_idx ON timetable_links(active,name);


CREATE UNIQUE INDEX timetable_links_one_active_plan_idx
    ON timetable_links(link_key) WHERE active=1;

ALTER TABLE source_cache
    ADD COLUMN availability TEXT NOT NULL DEFAULT 'available'
    CHECK (availability IN ('available','missing'));

ALTER TABLE source_cache ADD COLUMN http_status INTEGER;

DROP INDEX integrations_source_idx;

ALTER TABLE integrations DROP COLUMN source_url;

ALTER TABLE integrations ADD COLUMN timetable_link_key TEXT NOT NULL DEFAULT '';

CREATE INDEX integrations_timetable_link_idx ON integrations(timetable_link_key);
