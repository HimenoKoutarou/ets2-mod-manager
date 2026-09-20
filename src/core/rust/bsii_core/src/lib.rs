//! Bounds-checked BSII reader. It decodes the schema and walks every object,
//! retaining only a compact summary for the cross-language boundary.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BsiiHeader {
    pub version: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BsiiSummary {
    pub version: u32,
    pub definitions: u32,
    pub objects: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NumericField {
    pub object_index: usize,
    pub structure_name: String,
    pub field_name: String,
    pub type_id: u32,
    pub value: i64,
    pub offset: usize,
    pub size: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObjectField {
    pub name: String,
    pub type_id: u32,
    pub value: String,
    pub offset: usize,
    pub size: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObjectSummary {
    pub object_index: usize,
    pub structure_name: String,
    pub fields: Vec<ObjectField>,
}

pub fn inspect_header(bytes: &[u8]) -> Result<BsiiHeader, &'static str> {
    if bytes.len() < 8 || &bytes[..4] != b"BSII" {
        return Err("invalid_bsii_header");
    }
    let version = u32::from_le_bytes(bytes[4..8].try_into().unwrap());
    if !(1..=3).contains(&version) {
        return Err("unsupported_bsii_version");
    }
    Ok(BsiiHeader { version })
}

struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
    version: u32,
}
impl<'a> Reader<'a> {
    fn new(data: &'a [u8], version: u32) -> Self {
        Self {
            data,
            pos: 8,
            version,
        }
    }
    fn need(&self, n: usize) -> Result<(), String> {
        if self
            .pos
            .checked_add(n)
            .is_none_or(|end| end > self.data.len())
        {
            Err(format!("truncated_bsii_at_{}", self.pos))
        } else {
            Ok(())
        }
    }
    fn u8(&mut self) -> Result<u8, String> {
        self.need(1)?;
        let v = self.data[self.pos];
        self.pos += 1;
        Ok(v)
    }
    fn u16(&mut self) -> Result<u16, String> {
        self.need(2)?;
        let v = u16::from_le_bytes(self.data[self.pos..self.pos + 2].try_into().unwrap());
        self.pos += 2;
        Ok(v)
    }
    fn u32(&mut self) -> Result<u32, String> {
        self.need(4)?;
        let v = u32::from_le_bytes(self.data[self.pos..self.pos + 4].try_into().unwrap());
        self.pos += 4;
        Ok(v)
    }
    fn u64(&mut self) -> Result<u64, String> {
        self.need(8)?;
        let v = u64::from_le_bytes(self.data[self.pos..self.pos + 8].try_into().unwrap());
        self.pos += 8;
        Ok(v)
    }
    fn i16(&mut self) -> Result<i16, String> {
        Ok(self.u16()? as i16)
    }
    fn i32(&mut self) -> Result<i32, String> {
        Ok(self.u32()? as i32)
    }
    fn i64(&mut self) -> Result<i64, String> {
        Ok(self.u64()? as i64)
    }
    fn f32(&mut self) -> Result<f32, String> {
        Ok(f32::from_bits(self.u32()?))
    }
    fn string(&mut self) -> Result<String, String> {
        let n = self.u32()? as usize;
        self.need(n)?;
        let value = std::str::from_utf8(&self.data[self.pos..self.pos + n])
            .map_err(|_| "invalid_bsii_utf8".to_string())?
            .to_string();
        self.pos += n;
        Ok(value)
    }
}

fn encoded_string(reader: &mut Reader<'_>) -> Result<(), String> {
    reader.u64().map(|_| ())
}

fn encoded_token(reader: &mut Reader<'_>) -> Result<String, String> {
    let parts = reader.u8()?;
    if parts == 0xFF {
        return Ok(format!("{:016x}", reader.u64()?));
    }
    let mut values = Vec::with_capacity(parts as usize);
    for _ in 0..parts {
        values.push(format!("{:016x}", reader.u64()?));
    }
    Ok(values.join("."))
}

fn captured_value(reader: &mut Reader<'_>, ty: u32) -> Result<Option<String>, String> {
    match ty {
        0x01 => Ok(Some(reader.string()?)),
        0x03 => Ok(Some(encoded_token(reader)?)),
        0x27 | 0x2F => Ok(Some(reader.u32()?.to_string())),
        0x31 => Ok(Some(reader.i64()?.to_string())),
        0x35 => Ok(Some(reader.u8()?.to_string())),
        _ => {
            skip_value(reader, ty, 0)?;
            Ok(None)
        }
    }
}

/// Returns a bounded, read-only view of save objects. Unknown/complex field
/// types are skipped safely; callers can inspect them later without mutating
/// the original bytes.
pub fn inspect_objects(bytes: &[u8], max_objects: usize) -> Result<Vec<ObjectSummary>, String> {
    let header = inspect_header(bytes).map_err(str::to_string)?;
    let mut reader = Reader::new(bytes, header.version);
    let mut definitions: Vec<Option<(String, Vec<(String, u32, u32)>)>> = Vec::new();
    let mut result = Vec::new();
    while reader.pos < reader.data.len() && result.len() < max_objects {
        let block = reader.u32()?;
        if block == 0 {
            let valid = reader.u8()? != 0;
            if !valid {
                continue;
            }
            let id = reader.u32()?;
            let name = reader.string()?;
            let mut fields = Vec::new();
            loop {
                let ty = reader.u32()?;
                if ty == 0 {
                    break;
                }
                let field_name = reader.string()?;
                let mut ordinal_count = 0;
                if ty == 0x37 {
                    ordinal_count = reader.u32()?;
                    for _ in 0..ordinal_count {
                        reader.u32()?;
                        reader.string()?;
                    }
                }
                fields.push((field_name, ty, ordinal_count));
            }
            if id as usize >= definitions.len() {
                definitions.resize(id as usize + 1, None);
            }
            definitions[id as usize] = Some((name, fields));
            continue;
        }
        let Some((structure_name, fields)) =
            definitions.get(block as usize).and_then(Option::as_ref)
        else {
            return Err("unknown_bsii_structure".into());
        };
        let _object_id = encoded_token(&mut reader)?;
        let object_index = result.len();
        let mut captured = Vec::new();
        for (field_name, ty, ordinal_count) in fields {
            let offset = reader.pos;
            if let Some(value) = captured_value(&mut reader, *ty)? {
                let size = reader.pos.saturating_sub(offset);
                captured.push(ObjectField {
                    name: field_name.clone(),
                    type_id: *ty,
                    value,
                    offset,
                    size,
                });
            } else if *ty == 0x37 && *ordinal_count > 0 {
                // enum fields are already consumed by skip_value.
            }
        }
        result.push(ObjectSummary {
            object_index,
            structure_name: structure_name.clone(),
            fields: captured,
        });
    }
    Ok(result)
}
fn encoded_id(reader: &mut Reader<'_>) -> Result<(), String> {
    let parts = reader.u8()?;
    if parts == 0xFF {
        reader.u64()?;
    } else {
        for _ in 0..parts {
            encoded_string(reader)?;
        }
    }
    Ok(())
}
fn array<T>(
    reader: &mut Reader<'_>,
    mut f: impl FnMut(&mut Reader<'_>) -> Result<T, String>,
) -> Result<(), String> {
    let count = reader.u32()? as usize;
    for _ in 0..count {
        f(reader)?;
    }
    Ok(())
}
fn vecn<T>(
    reader: &mut Reader<'_>,
    count: usize,
    mut f: impl FnMut(&mut Reader<'_>) -> Result<T, String>,
) -> Result<(), String> {
    for _ in 0..count {
        f(reader)?;
    }
    Ok(())
}
fn skip_value(reader: &mut Reader<'_>, ty: u32, _ordinal_count: u32) -> Result<(), String> {
    match ty {
        0x01 => {
            reader.string()?;
        }
        0x02 => {
            array(reader, |r| r.string())?;
        }
        0x03 => {
            encoded_string(reader)?;
        }
        0x04 => {
            array(reader, encoded_string)?;
        }
        0x05 => {
            reader.f32()?;
        }
        0x06 => {
            array(reader, |r| r.f32())?;
        }
        0x07 => {
            vecn(reader, 2, |r| r.f32())?;
        }
        0x08 => {
            array(reader, |r| vecn(r, 2, |rr| rr.f32()))?;
        }
        0x09 => {
            vecn(reader, 3, |r| r.f32())?;
        }
        0x0A => {
            array(reader, |r| vecn(r, 3, |rr| rr.f32()))?;
        }
        0x11 => {
            vecn(reader, 3, |r| r.i32())?;
        }
        0x12 => {
            array(reader, |r| vecn(r, 3, |rr| rr.i32()))?;
        }
        0x17 => {
            vecn(reader, 4, |r| r.f32())?;
        }
        0x18 => {
            array(reader, |r| vecn(r, 4, |rr| rr.f32()))?;
        }
        0x19 => {
            vecn(reader, if reader.version >= 2 { 8 } else { 7 }, |r| r.f32())?;
        }
        0x1A => {
            let n = if reader.version >= 2 { 8 } else { 7 };
            array(reader, |r| vecn(r, n, |rr| rr.f32()))?;
        }
        0x25 => {
            reader.i32()?;
        }
        0x26 => {
            array(reader, |r| r.i32())?;
        }
        0x27 | 0x2F => {
            reader.u32()?;
        }
        0x28 => {
            array(reader, |r| r.u32())?;
        }
        0x29 => {
            reader.i16()?;
        }
        0x2A => {
            array(reader, |r| r.i16())?;
        }
        0x2B => {
            reader.u16()?;
        }
        0x2C => {
            array(reader, |r| r.u16())?;
        }
        0x31 => {
            reader.i64()?;
        }
        0x32 => {
            array(reader, |r| r.i64())?;
        }
        0x33 => {
            reader.u64()?;
        }
        0x34 => {
            array(reader, |r| r.u64())?;
        }
        0x35 => {
            reader.u8()?;
        }
        0x36 => {
            array(reader, |r| r.u8())?;
        }
        0x37 => {
            reader.u32()?;
        }
        0x39 | 0x3B | 0x3D => {
            encoded_id(reader)?;
        }
        0x3A | 0x3C | 0x3E => {
            array(reader, encoded_id)?;
        }
        _ => return Err(format!("unsupported_bsii_type_{ty:02x}")),
    }
    Ok(())
}

pub fn parse_summary(bytes: &[u8]) -> Result<BsiiSummary, String> {
    let header = inspect_header(bytes).map_err(str::to_string)?;
    let mut reader = Reader::new(bytes, header.version);
    let mut definitions: Vec<Vec<(u32, u32)>> = Vec::new();
    let mut definition_count = 0;
    let mut objects = 0;
    while reader.pos < reader.data.len() {
        let block = reader.u32()?;
        if block == 0 {
            let valid = reader.u8()? != 0;
            // Invalid definition blocks contain only the validity marker.
            // Do not consume an id/name/schema that is not present.
            if !valid {
                continue;
            }
            let id = reader.u32()?;
            let _name = reader.string()?;
            let mut fields = Vec::new();
            loop {
                let ty = reader.u32()?;
                if ty == 0 {
                    break;
                }
                let _field_name = reader.string()?;
                let mut ordinal_count = 0;
                if ty == 0x37 {
                    ordinal_count = reader.u32()?;
                    for _ in 0..ordinal_count {
                        reader.u32()?;
                        reader.string()?;
                    }
                }
                fields.push((ty, ordinal_count));
            }
            if id as usize >= definitions.len() {
                definitions.resize(id as usize + 1, Vec::new());
            }
            definitions[id as usize] = fields;
            definition_count += 1;
            continue;
        }
        let fields = definitions
            .get(block as usize)
            .ok_or_else(|| "unknown_bsii_structure".to_string())?;
        encoded_id(&mut reader)?;
        for (ty, ordinal_count) in fields {
            skip_value(&mut reader, *ty, *ordinal_count)?;
        }
        objects += 1;
    }
    Ok(BsiiSummary {
        version: header.version,
        definitions: definition_count,
        objects,
    })
}

/// Read the uniquely-addressable integer fields used by the save editor.
///
/// The parser still walks every field using the schema, but only materializes
/// UInt32/Int64 values whose names were requested. This keeps the C ABI and
/// Tauri boundary compact while retaining exact payload offsets for mutation.
pub fn find_numeric_fields(bytes: &[u8], wanted: &[&str]) -> Result<Vec<NumericField>, String> {
    let header = inspect_header(bytes).map_err(str::to_string)?;
    let wanted: std::collections::HashSet<&str> = wanted.iter().copied().collect();
    let mut reader = Reader::new(bytes, header.version);
    let mut definitions: Vec<Option<(String, Vec<(String, u32, u32)>)>> = Vec::new();
    let mut result = Vec::new();
    let mut object_index = 0usize;
    while reader.pos < reader.data.len() {
        let block = reader.u32()?;
        if block == 0 {
            let valid = reader.u8()? != 0;
            if !valid {
                continue;
            }
            let id = reader.u32()?;
            let name = reader.string()?;
            let mut fields = Vec::new();
            loop {
                let ty = reader.u32()?;
                if ty == 0 {
                    break;
                }
                let field_name = reader.string()?;
                let mut ordinal_count = 0;
                if ty == 0x37 {
                    ordinal_count = reader.u32()?;
                    for _ in 0..ordinal_count {
                        reader.u32()?;
                        reader.string()?;
                    }
                }
                fields.push((field_name, ty, ordinal_count));
            }
            if id as usize >= definitions.len() {
                definitions.resize(id as usize + 1, None);
            }
            definitions[id as usize] = Some((name, fields));
            continue;
        }

        let Some((structure_name, fields)) =
            definitions.get(block as usize).and_then(Option::as_ref)
        else {
            return Err("unknown_bsii_structure".into());
        };
        encoded_id(&mut reader)?;
        for (field_name, ty, ordinal_count) in fields {
            let offset = reader.pos;
            match *ty {
                0x27 | 0x2F => {
                    let value = reader.u32()? as i64;
                    if wanted.contains(field_name.as_str()) {
                        result.push(NumericField {
                            object_index,
                            structure_name: structure_name.clone(),
                            field_name: field_name.clone(),
                            type_id: *ty,
                            value,
                            offset,
                            size: 4,
                        });
                    }
                }
                0x31 => {
                    let value = reader.i64()?;
                    if wanted.contains(field_name.as_str()) {
                        result.push(NumericField {
                            object_index,
                            structure_name: structure_name.clone(),
                            field_name: field_name.clone(),
                            type_id: *ty,
                            value,
                            offset,
                            size: 8,
                        });
                    }
                }
                _ => skip_value(&mut reader, *ty, *ordinal_count)?,
            }
        }
        object_index += 1;
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn push_string(target: &mut Vec<u8>, value: &str) {
        target.extend_from_slice(&(value.len() as u32).to_le_bytes());
        target.extend_from_slice(value.as_bytes());
    }

    fn push_encoded_id(target: &mut Vec<u8>, parts: &[u64]) {
        target.push(parts.len() as u8);
        for part in parts {
            target.extend_from_slice(&part.to_le_bytes());
        }
    }

    fn numeric_fixture(experience: u32, money: i64) -> Vec<u8> {
        let mut data = b"BSII".to_vec();
        data.extend_from_slice(&3u32.to_le_bytes());

        // economy { bank: reference, experience_points: uint32 }
        data.extend_from_slice(&0u32.to_le_bytes());
        data.push(1);
        data.extend_from_slice(&1u32.to_le_bytes());
        push_string(&mut data, "economy");
        data.extend_from_slice(&0x39u32.to_le_bytes());
        push_string(&mut data, "bank");
        data.extend_from_slice(&0x27u32.to_le_bytes());
        push_string(&mut data, "experience_points");
        data.extend_from_slice(&0u32.to_le_bytes());

        // bank { money_account: int64 }
        data.extend_from_slice(&0u32.to_le_bytes());
        data.push(1);
        data.extend_from_slice(&2u32.to_le_bytes());
        push_string(&mut data, "bank");
        data.extend_from_slice(&0x31u32.to_le_bytes());
        push_string(&mut data, "money_account");
        data.extend_from_slice(&0u32.to_le_bytes());

        data.extend_from_slice(&1u32.to_le_bytes());
        push_encoded_id(&mut data, &[1]);
        push_encoded_id(&mut data, &[2]);
        data.extend_from_slice(&experience.to_le_bytes());

        data.extend_from_slice(&2u32.to_le_bytes());
        push_encoded_id(&mut data, &[2]);
        data.extend_from_slice(&money.to_le_bytes());
        data
    }
    #[test]
    fn reads_little_endian_version() {
        assert_eq!(inspect_header(b"BSII\x03\0\0\0").unwrap().version, 3);
    }
    #[test]
    fn rejects_unknown_version() {
        assert_eq!(
            inspect_header(b"BSII\x63\0\0\0"),
            Err("unsupported_bsii_version")
        );
    }

    #[test]
    fn numeric_field_query_rejects_non_bsii_input() {
        assert!(find_numeric_fields(b"not-bsii", &["money_account"]).is_err());
    }

    #[test]
    fn numeric_field_query_reads_typed_payloads_and_offsets() {
        let bytes = numeric_fixture(279_375, 1_253_729);
        let fields = find_numeric_fields(&bytes, &["money_account", "experience_points"])
            .expect("numeric fields");

        assert_eq!(fields.len(), 2);
        let experience = fields
            .iter()
            .find(|field| field.field_name == "experience_points")
            .expect("experience field");
        assert_eq!(experience.structure_name, "economy");
        assert_eq!(experience.type_id, 0x27);
        assert_eq!(experience.size, 4);
        assert_eq!(experience.value, 279_375);
        assert_eq!(
            &bytes[experience.offset..experience.offset + experience.size],
            &279_375u32.to_le_bytes()
        );

        let money = fields
            .iter()
            .find(|field| field.field_name == "money_account")
            .expect("money field");
        assert_eq!(money.structure_name, "bank");
        assert_eq!(money.type_id, 0x31);
        assert_eq!(money.size, 8);
        assert_eq!(money.value, 1_253_729);
        assert_eq!(
            &bytes[money.offset..money.offset + money.size],
            &1_253_729i64.to_le_bytes()
        );
    }

    #[test]
    fn skips_invalid_definition_blocks_without_cursor_drift() {
        let mut bytes = b"BSII".to_vec();
        bytes.extend_from_slice(&3u32.to_le_bytes());
        // Invalid definitions are encoded as a block marker plus a false
        // validity byte, followed immediately by the next block.
        bytes.extend_from_slice(&0u32.to_le_bytes());
        bytes.push(0);
        bytes.extend_from_slice(&0u32.to_le_bytes());
        bytes.push(1);
        bytes.extend_from_slice(&1u32.to_le_bytes());
        push_string(&mut bytes, "bank");
        bytes.extend_from_slice(&0x31u32.to_le_bytes());
        push_string(&mut bytes, "money_account");
        bytes.extend_from_slice(&0u32.to_le_bytes());
        bytes.extend_from_slice(&1u32.to_le_bytes());
        push_encoded_id(&mut bytes, &[1]);
        bytes.extend_from_slice(&123i64.to_le_bytes());

        let fields = find_numeric_fields(&bytes, &["money_account"]).expect("parse");
        assert_eq!(fields.len(), 1);
        assert_eq!(fields[0].value, 123);
    }
}
