pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var library
    required property var appearance
    required property string prefix
    property bool blocked: false
    property bool editing: false
    property string editId: ""
    property var modelState: library ? library.state : ({busy: false, items: [], selectedId: "", selected: {}})
    property var choices: [{id: "", name: qsTr("Без навыка")}].concat(modelState.items || [])
    enabled: !!library && !modelState.busy && !blocked
    spacing: appearance.dp(8)

    function beginEdit(skill) {
        editId = skill.id || "";
        skillName.text = skill.name || "";
        description.text = skill.description || "";
        instruction.text = skill.instruction || "";
        editing = true;
    }
    Connections {
        target: pane.library
        function onSaved(identifier) {
            if (pane.visible && pane.editing)
                pane.editId = identifier;
        }
    }
    Label {
        Layout.fillWidth: true
        text: qsTr("Навык для следующего запроса")
        font.bold: true
        wrapMode: Text.Wrap
    }
    ComboBox {
        objectName: pane.prefix + "SkillPick"
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        model: pane.choices
        textRole: "name"
        valueRole: "id"
        currentIndex: Math.max(0, pane.choices.findIndex(row => row.id === pane.modelState.selectedId))
        onActivated: pane.library.select(currentValue)
    }
    Label {
        Layout.fillWidth: true
        text: pane.modelState.selectedId
            ? qsTr("Будет передан выбранный навык: ") + (pane.modelState.selected.name || "") + ". " + (pane.modelState.selected.description || "")
            : qsTr("Навык не выбран. Сохранение в библиотеку само по себе его не включает.")
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }
    Label {
        Layout.fillWidth: true
        text: qsTr("Навык добавляет текстовые пожелания. Выбор или правка выбранного навыка сбросит текущий черновик и обсуждение. Файлы выбираются отдельно, изменения требуют подтверждения. После перезапуска навык нужно выбрать заново.")
        wrapMode: Text.Wrap
    }
    Flow {
        Layout.fillWidth: true
        spacing: pane.appearance.dp(6)
        Button {
            objectName: pane.prefix + "SkillNew"
            text: qsTr("+ Навык")
            onClicked: pane.beginEdit({})
        }
        Button {
            objectName: pane.prefix + "SkillEdit"
            text: qsTr("Редактировать выбранный")
            enabled: !!pane.modelState.selectedId
            onClicked: pane.beginEdit(pane.modelState.selected)
        }
        Button {
            objectName: pane.prefix + "SkillsReload"
            text: qsTr("Обновить навыки")
            onClicked: pane.library.load()
        }
    }
    Label {
        Layout.fillWidth: true
        visible: !!pane.modelState.error
        text: pane.modelState.error || ""
        color: pane.appearance.colors.warning
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }
    ColumnLayout {
        Layout.fillWidth: true
        visible: pane.editing
        Label {
            Layout.fillWidth: true
            text: pane.editId ? qsTr("Редактор сохранённого навыка") : qsTr("Новый навык")
            wrapMode: Text.Wrap
        }
        TextField {
            id: skillName
            objectName: pane.prefix + "SkillName"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            placeholderText: qsTr("Название · до 120 символов")
            Accessible.name: qsTr("Название навыка")
            maximumLength: 120
        }
        TextArea {
            id: description
            objectName: pane.prefix + "SkillDescription"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            placeholderText: qsTr("Описание · до 1000 символов")
            Accessible.name: qsTr("Описание навыка")
            textFormat: TextEdit.PlainText
            wrapMode: TextEdit.Wrap
        }
        TextArea {
            id: instruction
            objectName: pane.prefix + "SkillInstruction"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            placeholderText: qsTr("Инструкция · до 12000 символов. Например: разбивай лабораторную на подготовку, решение и проверку результата.")
            Accessible.name: qsTr("Инструкция навыка")
            textFormat: TextEdit.PlainText
            wrapMode: TextEdit.Wrap
            selectByMouse: true
        }
        Flow {
            Layout.fillWidth: true
            spacing: pane.appearance.dp(6)
            Button {
                objectName: pane.prefix + "SkillSave"
                text: qsTr("Сохранить навык")
                onClicked: pane.library.save(pane.editId, skillName.text, description.text, instruction.text)
            }
            Button {
                objectName: pane.prefix + "SkillEditorClose"
                text: qsTr("Закрыть редактор")
                onClicked: pane.editing = false
            }
        }
    }
}
