IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = 'SemanaIngenieriaDB')
BEGIN
    CREATE DATABASE SemanaIngenieriaDB;
END
GO

USE SemanaIngenieriaDB;
GO

-- Limpieza de tablas (AM)
DROP TABLE IF EXISTS LIDERA;
DROP TABLE IF EXISTS PRESENTA;
DROP TABLE IF EXISTS SESION;
DROP TABLE IF EXISTS PERSONA;
DROP TABLE IF EXISTS EVENTO;
DROP TABLE IF EXISTS LUGAR;
DROP TABLE IF EXISTS EMPRESA_ORG;
DROP TABLE IF EXISTS TIPO_ACTIVIDAD;
DROP TABLE IF EXISTS DEPARTAMENTO;
GO

-- DEPARTAMENTO
CREATE TABLE DEPARTAMENTO (
    Departamento_id VARCHAR(10) PRIMARY KEY,
    Nombre_departamento VARCHAR(100) NOT NULL UNIQUE
);

-- TIPO_ACTIVIDAD
CREATE TABLE TIPO_ACTIVIDAD (
    Tipo_actividad_id VARCHAR(10) PRIMARY KEY,
    Nombre_tipo VARCHAR(100) NOT NULL UNIQUE,
    Descripcion_tipo VARCHAR(255)
);

-- EMPRESA_ORG
CREATE TABLE EMPRESA_ORG (
    Empresa_id VARCHAR(10) PRIMARY KEY,
    Nombre VARCHAR(255) NOT NULL UNIQUE
);

-- LUGAR con constraints defensivos para Campus y Cupo
CREATE TABLE LUGAR (
    Lugar_id VARCHAR(10) PRIMARY KEY,
    Descripcion_lugar VARCHAR(255),
    Cupo INT NOT NULL CHECK (Cupo >= 0),
    Ubicacion VARCHAR(100),
    Campus VARCHAR(50) NOT NULL CHECK (Campus IN ('IIT', 'CU', 'CUAUH', 'N/A'))
);

-- EVENTO
CREATE TABLE EVENTO (
    Evento_id VARCHAR(10) PRIMARY KEY,
    Tipo_actividad_id VARCHAR(10) FOREIGN KEY REFERENCES TIPO_ACTIVIDAD(Tipo_actividad_id),
    Departamento_id VARCHAR(10) FOREIGN KEY REFERENCES DEPARTAMENTO(Departamento_id),
    Titulo_evento VARCHAR(255) NOT NULL,
    Descripcion_evento VARCHAR(MAX)
);

-- PERSONA con validacion de correo
CREATE TABLE PERSONA (
    Persona_id VARCHAR(10) PRIMARY KEY,
    Titulo VARCHAR(50),
    Primer_nombre VARCHAR(100) NOT NULL,
    Segundo_nombre VARCHAR(100),
    Primer_apellido VARCHAR(100),
    Segundo_apellido VARCHAR(100),
    Correo VARCHAR(150) CHECK (Correo LIKE '%@%' OR Correo = 'N/A')
);

-- SESION 
CREATE TABLE SESION (
    Sesion_id VARCHAR(10) PRIMARY KEY,
    Evento_id VARCHAR(10) FOREIGN KEY REFERENCES EVENTO(Evento_id),
    Lugar_id VARCHAR(10) FOREIGN KEY REFERENCES LUGAR(Lugar_id),
    Modalidad VARCHAR(20) NOT NULL CHECK (Modalidad IN ('Presencial', 'Virtual', 'Híbrida')),
    Fecha DATE NOT NULL CHECK (Fecha >= '2026-09-20' AND Fecha <= '2026-09-25'),
    Hora_inicio TIME NOT NULL,
    Hora_fin TIME NOT NULL,
    Link_reunion VARCHAR(MAX),
    Es_unica BIT NOT NULL,
    
    -- R1: Hora inicio debe ser menor a hora fin
    CONSTRAINT CHK_Horario CHECK (Hora_inicio < Hora_fin),
    
    -- SP02 / SP12: Lógica de modalidad y enlaces
    CONSTRAINT CHK_Modalidad_Logica CHECK (
        (Modalidad = 'Virtual' AND Link_reunion IS NOT NULL) OR
        (Modalidad IN ('Presencial', 'Híbrida'))
    )
);

-- PRESENTA
CREATE TABLE PRESENTA (
    Persona_id VARCHAR(10) FOREIGN KEY REFERENCES PERSONA(Persona_id),
    Evento_id VARCHAR(10) FOREIGN KEY REFERENCES EVENTO(Evento_id),
    Rol VARCHAR(50) NOT NULL CHECK (Rol IN ('Ponente', 'Maestro de ceremonias')),
    PRIMARY KEY (Persona_id, Evento_id)
);

-- LIDERA
CREATE TABLE LIDERA (
    Evento_id VARCHAR(10) FOREIGN KEY REFERENCES EVENTO(Evento_id),
    Empresa_id VARCHAR(10) FOREIGN KEY REFERENCES EMPRESA_ORG(Empresa_id),
    PRIMARY KEY (Evento_id, Empresa_id)
);
GO

CREATE TRIGGER TRG_Prevenir_Empalmes
ON SESION
AFTER INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    
    IF EXISTS (
        SELECT 1 
        FROM SESION S1
        INNER JOIN inserted I ON S1.Lugar_id = I.Lugar_id AND S1.Fecha = I.Fecha
        WHERE S1.Sesion_id <> I.Sesion_id
          AND (I.Hora_inicio < S1.Hora_fin AND I.Hora_fin > S1.Hora_inicio)
    )
    BEGIN
        RAISERROR ('Error de Integridad: Existe un empalme de horario en el mismo lugar para esta fecha.', 16, 1);
        ROLLBACK TRANSACTION;
    END
END;
GO

-- Creación de roles 
-- Admin
IF NOT EXISTS (SELECT * FROM sys.database_principals WHERE name = 'db_admin_proyecto' AND type = 'R')
BEGIN
    CREATE ROLE db_admin_proyecto;
END
GO

-- Viewer
IF NOT EXISTS (SELECT * FROM sys.database_principals WHERE name = 'db_lector_dashboard' AND type = 'R')
BEGIN
    CREATE ROLE db_lector_dashboard;
END
GO

-- Permiso de Admin
GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::dbo TO db_admin_proyecto;

-- Permiso Viewer
GRANT SELECT ON SCHEMA::dbo TO db_lector_dashboard;
GO

-- Asignaciones de roles, por mientras yo merengues
DECLARE @CurrentLogin NVARCHAR(128) = SUSER_SNAME(); -- User windows detect
DECLARE @SQL NVARCHAR(MAX);

-- En caso de falta de usuarios
IF NOT EXISTS (SELECT * FROM sys.database_principals WHERE name = @CurrentLogin)
BEGIN
    SET @SQL = 'CREATE USER ' + QUOTENAME(@CurrentLogin) + ' FOR LOGIN ' + QUOTENAME(@CurrentLogin) + ';';
    EXEC sp_executesql @SQL;
    PRINT 'Usuario de Windows creado en la base de datos: ' + @CurrentLogin;
END

-- Te asigna al rol de administrador
SET @SQL = 'ALTER ROLE db_admin_proyecto ADD MEMBER ' + QUOTENAME(@CurrentLogin) + ';';
EXEC sp_executesql @SQL;
PRINT 'Usuario ' + @CurrentLogin + ' asignado al rol db_admin_proyecto.';
GO