pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root
    objectName: "backtestView"

    required property var sessionState
    required property bool narrow
    required property var displayPreferences
    readonly property var rawReport: root.sessionState ? root.sessionState.backtest : null
    readonly property bool hasReport: root.rawReport !== null && root.rawReport !== undefined
    readonly property var report: root.hasReport ? root.rawReport : ({
        account_id: null,
        account_screen_name: null,
        draft_id: null,
        set_code: "",
        event_name: "",
        chosen_pick_count: 0,
        compared_count: 0,
        match_count: 0,
        skipped_count: 0,
        ranking_mode: "score",
        rows: []
    })
    readonly property string accountIdentity: {
        if (root.report.account_screen_name && root.report.account_id)
            return root.report.account_screen_name + " (" + root.report.account_id + ")"
        return root.report.account_screen_name || root.report.account_id || "Account unavailable"
    }
    readonly property string draftIdentity: root.report.draft_id || "Draft unavailable"

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gutter

        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Label { text: "Backtest report"; color: Theme.text; font.pixelSize: 22; font.bold: true }
                Label {
                    objectName: "backtestSubtitle"
                    Layout.fillWidth: true
                    text: root.hasReport
                        ? root.accountIdentity + " · Draft " + root.draftIdentity + " · "
                            + (root.report.set_code || "Set unavailable") + " · "
                            + (root.report.event_name || "Event unavailable") + " · "
                            + root.report.chosen_pick_count + " recorded picks"
                        : "Compare persisted picks with the active ranking"
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }
            Button {
                objectName: "backtestRunButton"
                text: root.hasReport ? "Run again" : "Run backtest"
                Accessible.name: text
                Accessible.description: "Requests a backtest through the shared live session."
                onClicked: sessionProvider.requestBacktest()
            }
        }

        StateBanner { Layout.fillWidth: true; sessionState: root.sessionState }

        Rectangle {
            visible: !root.hasReport
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.surfaceLow
            border.color: Theme.outline
            border.width: 1
            radius: Theme.radius
            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - 40, 440)
                spacing: 10
                Label { text: "No backtest available"; color: Theme.text; font.pixelSize: 20; font.bold: true }
                Label { Layout.fillWidth: true; text: "Complete or recover a draft to run a backtest. Published failures can be retried or dismissed above."; color: Theme.textMuted; wrapMode: Text.WordWrap }
            }
        }

        GridLayout {
            visible: root.hasReport
            Layout.fillWidth: true
            columns: root.narrow ? 2 : 4
            columnSpacing: 8
            rowSpacing: 8
            Repeater {
                model: [
                    { label: "COMPARED", value: root.report.compared_count },
                    { label: "MATCHED", value: root.report.match_count },
                    { label: "SKIPPED", value: root.report.skipped_count },
                    { label: "RANKING", value: root.report.ranking_mode === "score" ? "DO Score" : root.report.ranking_mode }
                ]
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 74
                    color: Theme.surfaceLow
                    border.color: Theme.outline
                    border.width: 1
                    radius: Theme.radius
                    Accessible.name: modelData.label + " " + modelData.value
                    ColumnLayout {
                        anchors.centerIn: parent
                        spacing: 3
                        Label { Layout.alignment: Qt.AlignHCenter; text: modelData.value; color: modelData.label === "MATCHED" ? Theme.primary : Theme.text; font.family: fixedFontFamily; font.pixelSize: root.narrow ? 16 : 20; font.bold: true }
                        Label { Layout.alignment: Qt.AlignHCenter; text: modelData.label; color: Theme.textMuted; font.pixelSize: 9; font.bold: true; font.letterSpacing: 0.8 }
                    }
                }
            }
        }

        Rectangle {
            visible: root.hasReport && root.report.skipped_count > 0
            Layout.fillWidth: true
            Layout.preferredHeight: skippedExplanation.implicitHeight + 22
            color: Theme.warningDark
            border.color: Theme.warning
            border.width: 1
            radius: Theme.radius
            Label {
                id: skippedExplanation
                anchors.fill: parent
                anchors.margins: 11
                text: root.report.skipped_count + " pick" + (root.report.skipped_count === 1 ? " was" : "s were") + " skipped because their recorded offered-card history is incomplete. Skipped picks do not affect the comparison."
                color: Theme.warning
                wrapMode: Text.WordWrap
                Accessible.name: text
            }
        }

        Rectangle {
            visible: root.hasReport
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.surfaceLow
            border.color: Theme.outline
            border.width: 1
            radius: Theme.radius
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 6
                RowLayout {
                    visible: !root.narrow
                    Layout.fillWidth: true
                    Label { Layout.preferredWidth: 74; text: "PICK"; color: Theme.textMuted; font.pixelSize: 10 }
                    Label { Layout.fillWidth: true; text: "RECOMMENDED"; color: Theme.textMuted; font.pixelSize: 10 }
                    Label { Layout.fillWidth: true; text: "ACTUAL"; color: Theme.textMuted; font.pixelSize: 10 }
                    Label { Layout.preferredWidth: 84; text: "RESULT"; color: Theme.textMuted; font.pixelSize: 10 }
                    Label { Layout.preferredWidth: 70; text: "DO"; color: Theme.textMuted; font.pixelSize: 10 }
                    Label {
                        objectName: "backtestWinRateHeader"
                        visible: root.displayPreferences.secondaryStats
                        Layout.preferredWidth: 70
                        text: "WR"
                        color: Theme.textMuted
                        font.pixelSize: 10
                    }
                    Label {
                        objectName: "backtestSourceHeader"
                        visible: root.displayPreferences.secondaryStats
                        Layout.preferredWidth: 90
                        text: "SOURCE"
                        color: Theme.textMuted
                        font.pixelSize: 10
                    }
                }
                ListView {
                    id: backtestRows
                    objectName: "backtestRows"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: root.displayPreferences.compactDensity ? 3 : 5
                    clip: true
                    model: root.report.rows
                    Accessible.name: "Backtest pick comparisons"
                    delegate: Rectangle {
                        id: backtestRow
                        required property int index
                        required property var modelData
                        readonly property bool skipped: modelData.match === null || modelData.match === undefined
                        readonly property string resultText: {
                            if (backtestRow.skipped)
                                return "Skipped"
                            return modelData.match ? "Match" : "Different"
                        }
                        readonly property string accessibleResult: {
                            if (backtestRow.skipped)
                                return "skipped: " + modelData.skipped_reason
                            return modelData.match ? "match" : "different"
                        }
                        readonly property color resultColor: {
                            if (modelData.match)
                                return Theme.primary
                            return backtestRow.skipped ? Theme.warning : Theme.textMuted
                        }
                        readonly property string comparisonText: {
                            if (!modelData.recommended)
                                return modelData.skipped_reason
                            const actualName = modelData.actual ? modelData.actual.name : "—"
                            return modelData.recommended.name + " → " + actualName
                        }
                        width: ListView.view.width
                        height: root.narrow ? (root.displayPreferences.secondaryStats ? 108 : 76) : 48
                        color: backtestRow.skipped ? "#30291f" : Theme.surface
                        border.color: Theme.outline
                        border.width: 1
                        radius: Theme.radius
                        Accessible.name: "Pick " + (modelData.pack_number + 1) + ", " + (modelData.pick_number + 1) + ", " + backtestRow.accessibleResult
                        RowLayout {
                            visible: !root.narrow
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 10
                            Label { Layout.preferredWidth: 74; text: "P" + (modelData.pack_number + 1) + " · P" + (modelData.pick_number + 1); color: Theme.textMuted; font.family: fixedFontFamily }
                            Label {
                                objectName: "backtestRecommended" + index
                                Layout.fillWidth: true
                                text: modelData.recommended ? modelData.recommended.name : modelData.skipped_reason
                                color: Theme.text
                                elide: Text.ElideRight
                            }
                            Label {
                                objectName: "backtestActual" + index
                                Layout.fillWidth: true
                                text: modelData.actual ? modelData.actual.name : "—"
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }
                            Label {
                                objectName: "backtestResult" + index
                                Layout.preferredWidth: 84
                                text: backtestRow.resultText
                                color: backtestRow.resultColor
                                font.bold: true
                            }
                            Label {
                                objectName: "backtestScore" + index
                                Layout.preferredWidth: 70
                                text: modelData.recommended_score !== null && modelData.recommended_score !== undefined ? modelData.recommended_score : "—"
                                color: Theme.text
                                font.family: fixedFontFamily
                            }
                            Label {
                                objectName: "backtestWinRate" + index
                                visible: root.displayPreferences.secondaryStats
                                Layout.preferredWidth: 70
                                text: modelData.recommended_win_rate !== null && modelData.recommended_win_rate !== undefined
                                    ? (modelData.recommended_win_rate * 100).toFixed(1) + "%" : "—"
                                color: Theme.textMuted
                                font.family: fixedFontFamily
                            }
                            Label {
                                objectName: "backtestSource" + index
                                visible: root.displayPreferences.secondaryStats
                                Layout.preferredWidth: 90
                                text: modelData.data_source || "—"
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }
                        }
                        ColumnLayout {
                            visible: root.narrow
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 2
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: "P" + (modelData.pack_number + 1) + " · P" + (modelData.pick_number + 1); color: Theme.textMuted; font.family: fixedFontFamily }
                                Label { Layout.fillWidth: true; text: backtestRow.resultText; color: modelData.match ? Theme.primary : Theme.warning; horizontalAlignment: Text.AlignRight }
                            }
                            Label { Layout.fillWidth: true; text: backtestRow.comparisonText; color: Theme.text; elide: Text.ElideRight }
                            Label {
                                objectName: "backtestNarrowScore" + index
                                Layout.fillWidth: true
                                text: "DO " + (modelData.recommended_score !== null && modelData.recommended_score !== undefined ? modelData.recommended_score : "—")
                                color: Theme.textMuted
                                font.pixelSize: 10
                            }
                            GridLayout {
                                objectName: "backtestNarrowSecondary" + index
                                visible: root.displayPreferences.secondaryStats
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: 8
                                rowSpacing: 1
                                Label { text: "WR " + (modelData.recommended_win_rate !== null && modelData.recommended_win_rate !== undefined ? (modelData.recommended_win_rate * 100).toFixed(1) + "%" : "—"); color: Theme.textMuted; font.pixelSize: 10 }
                                Label { text: "Pool " + (modelData.pool_size !== null && modelData.pool_size !== undefined ? modelData.pool_size : "—"); color: Theme.textMuted; font.pixelSize: 10 }
                                Label { text: "Offered " + (modelData.offered_count !== null && modelData.offered_count !== undefined ? modelData.offered_count : "—"); color: Theme.textMuted; font.pixelSize: 10 }
                                Label { Layout.columnSpan: 2; Layout.fillWidth: true; text: modelData.data_source || "History unavailable"; color: Theme.textMuted; font.pixelSize: 10; elide: Text.ElideRight }
                            }
                        }
                    }
                }
            }
        }
        Label { visible: root.hasReport; Layout.fillWidth: true; text: "This is an analytical comparison, not a player grade."; color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap }
    }
}
