-- The catalogue choice is a study year, not a semester.
DROP INDEX timetable_link_matchers_choice_idx;

ALTER TABLE timetable_link_matchers RENAME COLUMN semester TO study_year;

CREATE UNIQUE INDEX timetable_link_matchers_choice_idx
    ON timetable_link_matchers(degree,study_year,study_mode);
